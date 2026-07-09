"""SMS-2: deterministic SMS compliance gate (CustomerAcq-t90.2, blueprint §2-B2/§4.2/§5 RED LINE 3).

No provider enforces consent, recipient-timezone quiet hours, or frequency caps
automatically — this module is that enforcement, in code, ahead of the HELD
queue. It runs at BOTH enforcement points:

* **staging** — :func:`gate_at_staging` partitions a candidate batch before any
  draft reaches a human approver, and
* **send-time** — :func:`sms_send_eligibility` returns the same fail-closed
  ``(eligible, reason)`` shape as the studio send path's ``eligibility()``, so
  the send path re-checks every message at the moment of send.

Design invariants (the tests in ``tests/test_sms_gate.py`` prove each):

* **Pure and deterministic.** No model call, no environment read (TEST-MODE /
  ``SMS_REDIRECT_TO`` cannot alter the verdict), no clock read — ``now`` is an
  explicit input. Same inputs, same typed output.
* **Fail-closed.** Anything the gate cannot positively evaluate — no consent
  row, unresolvable recipient timezone, missing suppression ledger, missing
  registered 10DLC samples, naive datetimes, unknown trust tier — is a typed
  BLOCK, never a pass.
* **Honest reasons.** Every check emits a typed per-recipient
  :class:`Block`; ALL failing checks are reported (skip-with-reason), not just
  the first.

The 8 checks: (1) PEWC consent hard block, (2) cross-channel suppression,
(3) quiet hours by RECIPIENT timezone — federal 8am–9pm local, FL/OK/WA 8am–8pm
overlay, area-code + studio-affinity resolution, Hawaii no-DST via zoneinfo,
(4) opt-out language, (5) SHAFT / URL-shortener / prohibited-lending lint,
(6) registered 10DLC sample consistency (incl. BNPL terms must be registered),
(7) per-contact promo frequency cap, (8) pacing to 10DLC trust-score tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Sequence
from zoneinfo import ZoneInfo

__all__ = [
    "Block",
    "BlockCode",
    "ConsentRecord",
    "GateResult",
    "MessageContext",
    "RecipientContext",
    "SendContext",
    "StagingEntry",
    "StagingReport",
    "TRUST_TIER_DAILY_LIMITS",
    "TzResolution",
    "evaluate_sms",
    "gate_at_staging",
    "resolve_recipient_timezone",
    "sms_send_eligibility",
]


class BlockCode(str, Enum):
    """Typed per-recipient block reasons. ``*_UNEVALUABLE`` codes are the
    fail-closed family: the check could not be positively evaluated, so the
    message is blocked (never passed)."""

    NO_CONSENT = "no_consent"
    SUPPRESSED = "suppressed"
    SUPPRESSION_UNEVALUABLE = "suppression_unevaluable"
    QUIET_HOURS = "quiet_hours"
    TZ_UNRESOLVABLE = "tz_unresolvable"
    TIME_UNEVALUABLE = "time_unevaluable"
    MISSING_OPT_OUT_LANGUAGE = "missing_opt_out_language"
    SHAFT_TERM = "shaft_term"
    URL_SHORTENER = "url_shortener"
    PROHIBITED_LENDING = "prohibited_lending"
    SAMPLE_MISMATCH = "sample_mismatch"
    SAMPLES_UNAVAILABLE = "samples_unavailable"
    BNPL_NOT_REGISTERED = "bnpl_not_registered"
    FREQUENCY_CAP = "frequency_cap"
    FREQUENCY_UNEVALUABLE = "frequency_unevaluable"
    PACING_EXCEEDED = "pacing_exceeded"
    PACING_UNEVALUABLE = "pacing_unevaluable"


@dataclass(frozen=True)
class Block:
    """One typed block: which check fired, the machine-readable code, and a
    human-readable detail for the review queue."""

    code: BlockCode
    check: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    """The gate's verdict for one (recipient, message) pair. ``allowed`` is
    True only when EVERY check positively passed; ``blocks`` carries all
    failures (honest counts, not first-fail)."""

    allowed: bool
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class ConsentRecord:
    """One row of the consent table: prior express written consent (PEWC) with
    provenance. A row missing its source or timestamp is NOT valid PEWC."""

    phone: str
    sms_opt_in: bool
    source: str | None
    granted_at: datetime | None


@dataclass(frozen=True)
class RecipientContext:
    """Everything the gate needs to know about one recipient. ``suppressed``
    and ``recent_promo_sends`` are views of the cross-channel suppression
    ledger; ``None`` means the ledger could not be consulted (fail-closed)."""

    phone: str
    consent: ConsentRecord | None
    suppressed: bool | None
    recent_promo_sends: tuple[datetime, ...] | None
    studio_timezone: str | None = None


@dataclass(frozen=True)
class MessageContext:
    """The message body plus the registered 10DLC campaign samples it must be
    consistent with. ``registered_samples=None`` (or empty) means the sample
    set is unavailable — fail-closed."""

    body: str
    registered_samples: tuple[str, ...] | None


@dataclass(frozen=True)
class SendContext:
    """Batch-level send context. ``now`` MUST be timezone-aware — a naive
    ``now`` makes every time-dependent check un-evaluable (blocked)."""

    now: datetime
    trust_tier: str | None
    daily_quota_used: int | None
    frequency_max_sends: int = 1
    frequency_window_hours: int = 72


# ── quiet hours: recipient timezone resolution ────────────────────────────────

_PACIFIC = "America/Los_Angeles"
_EASTERN = "America/New_York"
_CENTRAL = "America/Chicago"
_MOUNTAIN = "America/Denver"
_ARIZONA = "America/Phoenix"
_HAWAII = "Pacific/Honolulu"
_ALASKA = "America/Anchorage"

# States with a stricter-than-federal quiet-hours overlay: 8am–8pm local.
_RESTRICTED_STATES = frozenset({"FL", "OK", "WA"})

_QUIET_START = time(8, 0)
_FEDERAL_END = time(21, 0)
_RESTRICTED_END = time(20, 0)


def _npa(state: str, zones: tuple[str, ...], *codes: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {code: (state, zones) for code in codes}


# NANP area code -> (state, candidate IANA zones). Split area codes list EVERY
# plausible zone; the window check must pass in ALL of them (fail-closed).
# Unlisted codes (incl. toll-free) resolve only via studio affinity, else block.
_AREA_CODES: dict[str, tuple[str, tuple[str, ...]]] = {
    **_npa("NV", (_PACIFIC,), "702", "725", "775"),
    **_npa("CA", (_PACIFIC,), "209", "213", "279", "310", "323", "341", "369",
           "408", "415", "424", "442", "510", "530", "559", "562", "619", "626",
           "628", "650", "657", "661", "669", "707", "714", "747", "760", "805",
           "818", "820", "831", "840", "858", "909", "916", "925", "949", "951"),
    **_npa("WA", (_PACIFIC,), "206", "253", "360", "425", "509", "564"),
    **_npa("OR", (_PACIFIC,), "458", "503", "971"),
    **_npa("OR", (_PACIFIC, _MOUNTAIN), "541"),
    **_npa("HI", (_HAWAII,), "808"),
    **_npa("AK", (_ALASKA,), "907"),
    **_npa("AZ", (_ARIZONA,), "480", "520", "602", "623", "928"),
    **_npa("ID", (_MOUNTAIN, _PACIFIC), "208", "986"),
    **_npa("UT", (_MOUNTAIN,), "385", "435", "801"),
    **_npa("CO", (_MOUNTAIN,), "303", "719", "720", "970", "983"),
    **_npa("NM", (_MOUNTAIN,), "505", "575"),
    **_npa("MT", (_MOUNTAIN,), "406"),
    **_npa("WY", (_MOUNTAIN,), "307"),
    **_npa("ND", (_CENTRAL, _MOUNTAIN), "701"),
    **_npa("SD", (_CENTRAL, _MOUNTAIN), "605"),
    **_npa("NE", (_CENTRAL,), "402", "531"),
    **_npa("NE", (_CENTRAL, _MOUNTAIN), "308"),
    **_npa("KS", (_CENTRAL,), "316", "913"),
    **_npa("KS", (_CENTRAL, _MOUNTAIN), "620", "785"),
    **_npa("OK", (_CENTRAL,), "405", "539", "572", "580", "918"),
    **_npa("TX", (_CENTRAL,), "210", "214", "254", "281", "325", "326", "346",
           "361", "409", "430", "432", "469", "512", "682", "713", "726", "737",
           "806", "817", "830", "832", "903", "936", "940", "945", "956", "972", "979"),
    **_npa("TX", (_MOUNTAIN,), "915"),
    **_npa("MN", (_CENTRAL,), "218", "320", "507", "612", "651", "763", "952"),
    **_npa("IA", (_CENTRAL,), "319", "515", "563", "641", "712"),
    **_npa("MO", (_CENTRAL,), "314", "417", "573", "636", "660", "816"),
    **_npa("AR", (_CENTRAL,), "479", "501", "870"),
    **_npa("LA", (_CENTRAL,), "225", "318", "337", "504", "985"),
    **_npa("MS", (_CENTRAL,), "228", "601", "662", "769"),
    **_npa("AL", (_CENTRAL,), "205", "251", "256", "334", "659"),
    **_npa("WI", (_CENTRAL,), "262", "414", "534", "608", "715", "920"),
    **_npa("IL", (_CENTRAL,), "217", "224", "309", "312", "331", "447", "618",
           "630", "708", "773", "779", "815", "847", "872"),
    **_npa("TN", (_CENTRAL,), "615", "629", "731", "901", "931"),
    **_npa("TN", (_EASTERN,), "423", "865"),
    **_npa("KY", (_CENTRAL,), "270", "364"),
    **_npa("KY", (_EASTERN,), "502", "606", "859"),
    **_npa("IN", (_EASTERN,), "260", "317", "463", "574", "765", "930"),
    **_npa("IN", (_CENTRAL,), "219"),
    **_npa("IN", (_EASTERN, _CENTRAL), "812"),
    **_npa("MI", (_EASTERN,), "231", "248", "269", "313", "517", "586", "616",
           "734", "810", "947", "989"),
    **_npa("MI", (_EASTERN, _CENTRAL), "906"),
    **_npa("OH", (_EASTERN,), "216", "220", "234", "330", "380", "419", "440",
           "513", "614", "740", "937"),
    **_npa("GA", (_EASTERN,), "229", "404", "470", "478", "678", "706", "762",
           "770", "912"),
    **_npa("FL", (_EASTERN,), "239", "305", "321", "352", "386", "407", "561",
           "656", "689", "727", "754", "772", "786", "813", "863", "904", "941", "954"),
    **_npa("FL", (_EASTERN, _CENTRAL), "850"),
    **_npa("SC", (_EASTERN,), "803", "839", "843", "854", "864"),
    **_npa("NC", (_EASTERN,), "252", "336", "704", "743", "828", "910", "919",
           "980", "984"),
    **_npa("VA", (_EASTERN,), "276", "434", "540", "571", "703", "757", "804"),
    **_npa("WV", (_EASTERN,), "304", "681"),
    **_npa("MD", (_EASTERN,), "240", "301", "410", "443", "667"),
    **_npa("DE", (_EASTERN,), "302"),
    **_npa("DC", (_EASTERN,), "202"),
    **_npa("PA", (_EASTERN,), "215", "223", "267", "272", "412", "445", "484",
           "570", "610", "717", "724", "814", "878"),
    **_npa("NJ", (_EASTERN,), "201", "551", "609", "640", "732", "848", "856",
           "862", "908", "973"),
    **_npa("NY", (_EASTERN,), "212", "315", "332", "347", "516", "518", "585",
           "607", "631", "646", "680", "716", "718", "838", "845", "914", "917",
           "929", "934"),
    **_npa("CT", (_EASTERN,), "203", "475", "860", "959"),
    **_npa("RI", (_EASTERN,), "401"),
    **_npa("MA", (_EASTERN,), "339", "351", "413", "508", "617", "774", "781",
           "857", "978"),
    **_npa("VT", (_EASTERN,), "802"),
    **_npa("NH", (_EASTERN,), "603"),
    **_npa("ME", (_EASTERN,), "207"),
}


@dataclass(frozen=True)
class TzResolution:
    """How a recipient's local clock was resolved. ``state=None`` means the
    state is unknown (studio-affinity fallback) — the stricter 8am–8pm window
    applies because FL/OK/WA membership cannot be ruled out."""

    zones: tuple[str, ...]
    state: str | None
    via: str


def resolve_recipient_timezone(
    phone: str, *, studio_timezone: str | None
) -> TzResolution | None:
    """Resolve a recipient's candidate timezone(s): NANP area code first, the
    studio's timezone as an affinity fallback, ``None`` when neither works
    (the caller must block — fail-closed). DST vs no-DST (Hawaii, Arizona) is
    inherent in the IANA zones, not special-cased here."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        hit = _AREA_CODES.get(digits[:3])
        if hit is not None:
            state, zones = hit
            return TzResolution(zones=zones, state=state, via="area_code")
    if studio_timezone:
        try:
            ZoneInfo(studio_timezone)
        except Exception:
            return None
        return TzResolution(zones=(studio_timezone,), state=None, via="studio_affinity")
    return None


# ── content lint: opt-out language, SHAFT, shorteners, lending, BNPL ─────────

_OPT_OUT_RE = re.compile(r"\b(?:reply|text)\s+stop\b", re.IGNORECASE)

# Lintable SHAFT classes (word/phrase-boundary matched): alcohol, tobacco/vape,
# cannabis, firearms, adult, gambling. Hate is not reliably lintable by wordlist
# and stays with the human review queue.
_SHAFT_TERMS = (
    "alcohol", "beer", "wine", "whiskey", "whisky", "vodka", "tequila", "rum",
    "booze", "tobacco", "cigarette", "cigar", "nicotine", "vape", "vaping",
    "e-cig", "cbd", "thc", "cannabis", "marijuana", "weed", "kush",
    "firearm", "firearms", "gun", "guns", "rifle", "pistol", "ammo",
    "ammunition", "porn", "xxx", "nude", "escort", "casino", "betting",
    "lottery", "jackpot", "poker",
)

# Payday / third-party lending is prohibited on 10DLC regardless of samples.
_LENDING_TERMS = ("payday", "payday loan", "cash advance", "title loan")

# BNPL providers: allowed ONLY when the registered samples use the same term
# (vetter treatment of merchant BNPL is unverified — keep it sample-consistent).
_BNPL_TERMS = ("klarna", "affirm", "afterpay", "sezzle")

_SHORTENER_RE = re.compile(
    r"\b(?:bit\.ly|tinyurl\.com|goo\.gl|t\.co|ow\.ly|is\.gd|buff\.ly"
    r"|rebrand\.ly|cutt\.ly|rb\.gy|tiny\.cc|shorturl\.at|s\.id|v\.gd|soo\.gd)\b",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z]+")

# Minimum best-sample Jaccard similarity for "consistent with the registered
# 10DLC campaign samples".
_SAMPLE_SIMILARITY_MIN = 0.5


def _term_re(term: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in term.split()]
    return re.compile(r"\b" + r"\s+".join(words) + r"\b", re.IGNORECASE)


_SHAFT_RES = tuple(_term_re(t) for t in _SHAFT_TERMS)
_LENDING_RES = tuple(_term_re(t) for t in _LENDING_TERMS)
_BNPL_RES = tuple((t, _term_re(t)) for t in _BNPL_TERMS)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(_URL_RE.sub(" ", text.lower())))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


# ── check 8: pacing to 10DLC trust-score tier ────────────────────────────────

# Daily brand send allowance by trust-score tier (T-Mobile-style daily brand
# limits). An unknown tier is un-evaluable — blocked.
TRUST_TIER_DAILY_LIMITS: dict[str, int] = {
    "low": 2_000,
    "medium": 10_000,
    "high": 40_000,
    "top": 200_000,
}


# ── the gate ─────────────────────────────────────────────────────────────────


def _check_consent(recipient: RecipientContext) -> list[Block]:
    c = recipient.consent
    check = "consent_pewc"
    if c is None:
        return [Block(BlockCode.NO_CONSENT, check, "no consent row for recipient")]
    problems = []
    if not c.sms_opt_in:
        problems.append("no sms opt-in")
    if not (c.source or "").strip():
        problems.append("consent source missing")
    if c.granted_at is None or c.granted_at.tzinfo is None:
        problems.append("consent timestamp missing or naive")
    if problems:
        return [Block(BlockCode.NO_CONSENT, check, "; ".join(problems))]
    return []


def _check_suppression(recipient: RecipientContext) -> list[Block]:
    check = "suppression_ledger"
    if recipient.suppressed is None:
        return [Block(
            BlockCode.SUPPRESSION_UNEVALUABLE, check,
            "suppression ledger unavailable — fail closed",
        )]
    if recipient.suppressed:
        return [Block(BlockCode.SUPPRESSED, check, "recipient is on the suppression ledger")]
    return []


def _check_quiet_hours(recipient: RecipientContext, now: datetime) -> list[Block]:
    check = "quiet_hours"
    resolution = resolve_recipient_timezone(
        recipient.phone, studio_timezone=recipient.studio_timezone
    )
    if resolution is None:
        return [Block(
            BlockCode.TZ_UNRESOLVABLE, check,
            f"cannot resolve a timezone for {recipient.phone!r} — fail closed",
        )]
    # Unknown state (affinity fallback) cannot rule out FL/OK/WA — apply the
    # stricter window.
    restricted = resolution.state is None or resolution.state in _RESTRICTED_STATES
    end = _RESTRICTED_END if restricted else _FEDERAL_END
    for zone in resolution.zones:
        local = now.astimezone(ZoneInfo(zone))
        t = local.time()
        if not (_QUIET_START <= t < end):
            return [Block(
                BlockCode.QUIET_HOURS, check,
                f"{local.strftime('%H:%M')} local ({zone}, via {resolution.via}) is outside "
                f"{_QUIET_START.strftime('%H:%M')}-{end.strftime('%H:%M')}",
            )]
    return []


def _check_opt_out_language(message: MessageContext) -> list[Block]:
    if _OPT_OUT_RE.search(message.body):
        return []
    return [Block(
        BlockCode.MISSING_OPT_OUT_LANGUAGE, "opt_out_language",
        'message lacks opt-out language (e.g. "Reply STOP to opt out")',
    )]


def _check_content_lint(message: MessageContext) -> list[Block]:
    check = "shaft_lint"
    blocks = []
    for pattern in _SHAFT_RES:
        hit = pattern.search(message.body)
        if hit:
            blocks.append(Block(BlockCode.SHAFT_TERM, check, f"SHAFT term {hit.group(0)!r}"))
            break
    hit = _SHORTENER_RE.search(message.body)
    if hit:
        blocks.append(Block(
            BlockCode.URL_SHORTENER, check,
            f"public URL shortener {hit.group(0)!r} (carrier error-30007 class)",
        ))
    for pattern in _LENDING_RES:
        hit = pattern.search(message.body)
        if hit:
            blocks.append(Block(
                BlockCode.PROHIBITED_LENDING, check,
                f"prohibited lending language {hit.group(0)!r}",
            ))
            break
    return blocks


def _check_sample_consistency(message: MessageContext) -> list[Block]:
    check = "registered_samples"
    samples = message.registered_samples
    if not samples:
        return [Block(
            BlockCode.SAMPLES_UNAVAILABLE, check,
            "registered 10DLC campaign samples unavailable — fail closed",
        )]
    blocks = []
    body_tokens = _tokens(message.body)
    best = max(_jaccard(body_tokens, _tokens(s)) for s in samples)
    if best < _SAMPLE_SIMILARITY_MIN:
        blocks.append(Block(
            BlockCode.SAMPLE_MISMATCH, check,
            f"best similarity to registered samples {best:.2f} < {_SAMPLE_SIMILARITY_MIN}",
        ))
    for term, pattern in _BNPL_RES:
        if pattern.search(message.body) and not any(pattern.search(s) for s in samples):
            blocks.append(Block(
                BlockCode.BNPL_NOT_REGISTERED, check,
                f"BNPL term {term!r} does not appear in any registered sample",
            ))
    return blocks


def _check_frequency_cap(recipient: RecipientContext, ctx: SendContext) -> list[Block]:
    check = "frequency_cap"
    sends = recipient.recent_promo_sends
    if sends is None:
        return [Block(
            BlockCode.FREQUENCY_UNEVALUABLE, check,
            "send history unavailable (ledger window) — fail closed",
        )]
    if any(s.tzinfo is None for s in sends):
        return [Block(
            BlockCode.FREQUENCY_UNEVALUABLE, check,
            "send history contains naive timestamps — fail closed",
        )]
    window_start = ctx.now - timedelta(hours=ctx.frequency_window_hours)
    in_window = sum(1 for s in sends if s > window_start)
    if in_window >= ctx.frequency_max_sends:
        return [Block(
            BlockCode.FREQUENCY_CAP, check,
            f"{in_window} promo send(s) in the last {ctx.frequency_window_hours}h "
            f"(cap {ctx.frequency_max_sends})",
        )]
    return []


def _check_pacing(ctx: SendContext) -> list[Block]:
    check = "trust_tier_pacing"
    limit = TRUST_TIER_DAILY_LIMITS.get(ctx.trust_tier or "")
    if limit is None or ctx.daily_quota_used is None:
        return [Block(
            BlockCode.PACING_UNEVALUABLE, check,
            f"trust tier {ctx.trust_tier!r} / quota used {ctx.daily_quota_used!r} "
            "un-evaluable — fail closed",
        )]
    if ctx.daily_quota_used >= limit:
        return [Block(
            BlockCode.PACING_EXCEEDED, check,
            f"daily quota exhausted ({ctx.daily_quota_used}/{limit} for tier "
            f"{ctx.trust_tier!r})",
        )]
    return []


def evaluate_sms(
    recipient: RecipientContext, message: MessageContext, ctx: SendContext
) -> GateResult:
    """Run all 8 compliance checks for one (recipient, message) pair and return
    the typed verdict. Pure and deterministic: never reads the environment or
    the clock, so TEST-MODE (``SMS_REDIRECT_TO``) cannot change the outcome.
    Collects EVERY failing check; fail-closed on anything un-evaluable."""
    blocks: list[Block] = []
    time_ok = ctx.now.tzinfo is not None
    if not time_ok:
        blocks.append(Block(
            BlockCode.TIME_UNEVALUABLE, "clock",
            "SendContext.now is naive — time-dependent checks un-evaluable, fail closed",
        ))
    blocks += _check_consent(recipient)
    blocks += _check_suppression(recipient)
    if time_ok:
        blocks += _check_quiet_hours(recipient, ctx.now)
        blocks += _check_frequency_cap(recipient, ctx)
    blocks += _check_opt_out_language(message)
    blocks += _check_content_lint(message)
    blocks += _check_sample_consistency(message)
    blocks += _check_pacing(ctx)
    return GateResult(allowed=not blocks, blocks=tuple(blocks))


@dataclass(frozen=True)
class StagingEntry:
    """One staged candidate's verdict, keyed back to the batch by index."""

    index: int
    phone: str
    result: GateResult


@dataclass(frozen=True)
class StagingReport:
    """The staging partition: entries in input order plus honest counts
    (``n_eligible + n_blocked`` always equals the batch size)."""

    entries: tuple[StagingEntry, ...]
    n_eligible: int
    n_blocked: int


def gate_at_staging(
    items: Sequence[tuple[RecipientContext, MessageContext]], ctx: SendContext
) -> StagingReport:
    """STAGING enforcement point: gate a candidate batch BEFORE the HELD queue,
    so a blocked draft never reaches a human approver. Returns a per-recipient
    partition with typed reasons and honest counts."""
    entries = tuple(
        StagingEntry(index=i, phone=recipient.phone,
                     result=evaluate_sms(recipient, message, ctx))
        for i, (recipient, message) in enumerate(items)
    )
    n_eligible = sum(1 for e in entries if e.result.allowed)
    return StagingReport(
        entries=entries, n_eligible=n_eligible, n_blocked=len(entries) - n_eligible
    )


def sms_send_eligibility(
    recipient: RecipientContext, message: MessageContext, ctx: SendContext
) -> tuple[bool, str]:
    """SEND-TIME enforcement point: the ``(eligible, reason)`` adapter matching
    the studio send path's ``eligibility()`` protocol, so ``send_eligible`` /
    ``approve_and_publish`` re-run the FULL gate at the moment of send. The
    sandbox redirect never bypasses this — the gate does not read it."""
    result = evaluate_sms(recipient, message, ctx)
    if result.allowed:
        return True, "sms compliance gate: all checks passed"
    return False, "; ".join(f"{b.code.value}: {b.detail}" for b in result.blocks)
