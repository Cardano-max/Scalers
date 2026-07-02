"""Example-grounded campaign generation (CustomerAcq-ju1.4).

Generates a campaign for a chosen artist that visibly BUILDS ON that artist's real past
examples (offer, CTA, tone, structure) — so drafts read like this client's best sends,
improved, not generic template mail. Deterministic + keyless-first (no model call
required): the client's real style is transcribed in ju1.2's example library, so we
mirror it in code and only need an LLM to embellish, never to invent.

Discipline (the anti-theater posture this whole epic enforces):
  * OFFER: a price/discount appears ONLY when the operator/interview supplied it (or a
    substantiated real offers doc) — never invented, never lifted from a historical
    example as if it were the current offer. No offer given -> NO price/discount in copy
    (65w.14).
  * SCARCITY COUNT: "N spots left" uses N ONLY when the operator gave a real spot count —
    otherwise a generic "spots are limited" (a fabricated number is the exact theater we
    refuse).
  * CHANNEL: the client's channel is SMS. We produce SMS-shaped drafts (opt-out line
    ALWAYS present, length bounded) + an email variant. There is NO SMS send path in this
    system yet — every SMS draft carries an honest ``send_status='no-send-path'`` badge;
    we never fake a connector (test mode blocks sends regardless).
  * ZERO EXAMPLES: an artist with no examples of their own is generated from tenant-level
    patterns, and the campaign says so honestly — never a fabricated example.
  * ARTWORK: attachment requested but no asset on file -> an honest flag, never a
    fabricated asset reference.

Campaign copy is CAMPAIGN-LEVEL (a broadcast to a segment), so it makes no per-customer
claims at all — it references the artist + the operator's offer, both real. That keeps it
inside the ju1.3 anti-fake-personalization guard by construction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# SMS shape bounds observed in the client's real sends (110-420 chars; a 142-char
# follow-up, a 413-char opener). We keep drafts inside this envelope.
SMS_MIN_CHARS = 110
SMS_MAX_CHARS = 420

# The client's real opt-out line (every one of their sends carries a reply-STOP).
OPT_OUT_LINE = "Reply STOP to opt out"

# Honest badge: there is no SMS connector in this system (verified: no connectors/sms.py,
# publish() marks an sms action 'failed: unknown channel'). SMS drafts are review-only.
SMS_SEND_STATUS = "no-send-path"
EMAIL_SEND_STATUS = "review-queue"


@dataclass(frozen=True)
class GeneratedDraft:
    """One generated campaign message, grounded in real examples."""

    channel: str                        # 'sms' | 'email'
    role: str                           # 'opener' | 'follow_up'
    body: str
    subject: str | None = None          # email only
    grounded_example_ids: list[str] = field(default_factory=list)
    offer_price_usd: int | None = None  # the operator's offer, if any (never invented)
    send_status: str = SMS_SEND_STATUS
    notes: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.body)


@dataclass(frozen=True)
class GeneratedCampaign:
    """A generated campaign for one artist: a pre-generation pattern summary + the drafts
    (opener + optional scarcity follow-up, each in SMS and email), all example-grounded."""

    tenant_id: str
    artist: str
    pattern_summary: str
    drafts: list[GeneratedDraft]
    grounded_example_ids: list[str]
    has_artist_examples: bool
    offer_price_usd: int | None = None
    notes: list[str] = field(default_factory=list)


def _keyword(artist: str) -> str:
    """The reply keyword for an artist, mirroring the client's real 'Reply ANGEL' CTA —
    the artist's first name, uppercased and letters-only."""
    first = (artist or "").strip().split()[0] if (artist or "").strip() else ""
    return re.sub(r"[^A-Za-z0-9]", "", first).upper() or "BOOK"


def _price_str(price: int | None) -> str:
    return f"${price:,}" if isinstance(price, int) and price > 0 else ""


# --------------------------------------------------------------------------- #
# Pre-generation pattern summary — grounded in ju1.2's real example library.
# --------------------------------------------------------------------------- #
def summarize_patterns(tenant_id: str, artist: str | None = None, *, dsn: str | None = None) -> str:
    """The honest pre-generation summary the supervisor states BEFORE drafting: what real
    campaigns exist for this artist and the strongest reusable patterns, grounded in
    ju1.2's example library. Zero examples for the artist -> says so and falls back to
    tenant-level patterns. Never invents a campaign or a pattern."""
    from studio.campaign_examples_store import get_examples, get_patterns

    try:
        artist_examples = get_examples(tenant_id, artist=artist, dsn=dsn) if artist else []
        all_patterns = get_patterns(tenant_id, dsn=dsn)
    except Exception:
        artist_examples, all_patterns = [], []

    who = artist or "this studio"
    lines: list[str]
    if artist and artist_examples:
        names = ", ".join(e["campaign_name"] for e in artist_examples)
        lines = [
            f"I found {len(artist_examples)} previous campaign(s) for {who}: {names}. "
            "I'll build on what actually worked for them, improved — not generic mail."
        ]
    elif artist:
        lines = [
            f"I have NO previous campaigns on file specifically for {who}, so I can't cite "
            f"{who}'s own past sends. I'll ground this in the studio's overall proven "
            "patterns instead, and say so honestly."
        ]
    else:
        lines = ["I'll ground this in the studio's overall proven campaign patterns."]

    # The strongest reusable patterns, described from the REAL pattern rows (evidence-backed).
    reusable = _reusable_patterns(all_patterns)
    if reusable:
        lines.append("Strongest reusable patterns from this client's real sends:")
        lines.extend(f"  {i}. {d}" for i, d in enumerate(reusable, 1))
    return "\n".join(lines)


# The pattern keys we surface as "reusable plays", in priority order, with a short gloss
# derived from the real pattern row's detail (prices etc.) — never a hardcoded claim.
_REUSABLE_ORDER: tuple[str, ...] = (
    "artist_special", "price_anchor", "limited_spots_scarcity", "payment_plan_angle",
    "reply_artist_cta", "opener_followup_sequence", "artwork_attachment_on_opener",
)

_REUSABLE_GLOSS: dict[str, str] = {
    "artist_special": "artist-fronted full-day special",
    "price_anchor": "up-front price anchor",
    "limited_spots_scarcity": "limited-spots scarcity (counted down in the follow-up)",
    "payment_plan_angle": "payment-plan angle (Klarna/Affirm)",
    "reply_artist_cta": "reply-the-artist's-name keyword CTA",
    "opener_followup_sequence": "opener + scarcity follow-up sequence",
    "artwork_attachment_on_opener": "artwork image on the opener",
}


def _reusable_patterns(patterns: list[dict[str, Any]]) -> list[str]:
    by_key = {p["pattern_key"]: p for p in patterns}
    out: list[str] = []
    for key in _REUSABLE_ORDER:
        p = by_key.get(key)
        if not p:
            continue
        gloss = _REUSABLE_GLOSS[key]
        if key == "price_anchor":
            prices = (p.get("detail") or {}).get("prices") or []
            if prices:
                gloss += " (past specials: " + ", ".join(f"${x:,}" for x in prices) + ")"
        out.append(gloss)
    return out


# --------------------------------------------------------------------------- #
# Generation — deterministic, keyless, in the client's real SMS style.
# --------------------------------------------------------------------------- #
def _clamp_sms(body: str) -> str:
    """Keep an SMS body inside the client's observed length envelope. Only ever TRIMS
    (never pads with filler) and always preserves the opt-out line."""
    if len(body) <= SMS_MAX_CHARS:
        return body
    marker = "\n\n" + OPT_OUT_LINE
    head, sep, _tail = body.rpartition(marker)
    if sep and head:
        keep = SMS_MAX_CHARS - len(marker)
        return head[:keep].rstrip() + marker
    return body[:SMS_MAX_CHARS]


def _opener_sms(artist: str, price: int | None, spots: int | None, payment_plan: str | None) -> str:
    kw = _keyword(artist)
    ps = _price_str(price)
    lead = f"{artist} has opened a booking window for returning clients."
    if spots and spots > 0:
        lead += f" Limited to {spots} spots."
    if ps:
        lead += f" Full-day sessions at {ps} per session."
    paras = [f"{artist.upper()} FULL-DAY SPECIAL", lead]
    if payment_plan:
        paras.append(f"{payment_plan} payment plans are available if you'd rather split it up.")
    paras.append(f"Reply {kw} to check availability or get a quote.")
    paras.append(OPT_OUT_LINE)
    return _clamp_sms("\n\n".join(paras))


def _followup_sms(artist: str, price: int | None, spots: int | None) -> str:
    kw = _keyword(artist)
    ps = _price_str(price)
    special = f"{artist}'s {ps} full-day special" if ps else f"{artist}'s full-day special"
    if spots and spots > 0:
        lead = f"We are DOWN to {spots} SPOTS LEFT for {special}."
    else:
        lead = f"Spots are limited for {special}."
    return _clamp_sms("\n\n".join([
        f"{lead} Text {kw} now to claim your spot before they're gone.",
        OPT_OUT_LINE,
    ]))


def _opener_email(artist: str, price: int | None, spots: int | None,
                  payment_plan: str | None) -> tuple[str, str]:
    kw = _keyword(artist)
    ps = _price_str(price)
    subject = f"{artist} full-day special — a booking window for returning clients"
    offer_clause = ""
    if ps:
        offer_clause += f", with full-day sessions at {ps} per session"
    if spots and spots > 0:
        offer_clause += f" (limited to {spots} spots)"
    paras = [
        "Hi there,",
        f"{artist} has opened a short booking window for returning clients{offer_clause}. "
        "Great for larger pieces, finishing work, or starting something new.",
    ]
    if payment_plan:
        paras.append(f"{payment_plan} payment plans are available if you'd rather split it up.")
    paras.append(f"Reply {kw} (or just reply to this email) to check availability or get a quote.")
    paras.append(OPT_OUT_LINE + ".")
    return subject, "\n\n".join(paras)


def _followup_email(artist: str, price: int | None, spots: int | None) -> tuple[str, str]:
    kw = _keyword(artist)
    ps = _price_str(price)
    subject = f"{artist} full-day special — spots are going"
    scarcity = f"We're down to {spots} spots" if spots and spots > 0 else "Spots are limited"
    special = f"{artist}'s full-day special" + (f" at {ps}" if ps else "")
    body = "\n\n".join([
        "Hi there,",
        f"{scarcity} for {special}. Text {kw} now to claim your spot before they're gone.",
        OPT_OUT_LINE + ".",
    ])
    return subject, body


def generate_campaign(
    tenant_id: str,
    *,
    artist: str,
    offer_price_usd: int | None = None,
    offer_type: str | None = None,
    payment_plan: str | None = None,
    spots: int | None = None,
    channels: tuple[str, ...] = ("sms", "email"),
    follow_up: bool = True,
    attach_artwork: bool = False,
    dsn: str | None = None,
) -> GeneratedCampaign:
    """Generate an example-grounded campaign for ``artist``.

    Mirrors the artist's REAL opener (and follow-up) examples in the client's SMS style,
    improved. Offer discipline: ``offer_price_usd``/``payment_plan`` appear in copy ONLY
    when the operator supplied them; ``spots`` gates the scarcity count (never invented).
    Produces an opener (+ optional scarcity follow-up), each as an SMS draft (opt-out
    always, length-bounded, honest no-send badge) and an email variant. Zero examples for
    the artist -> generated from tenant-level patterns with an honest note. Deterministic;
    no model call required."""
    from studio.campaign_examples_store import get_examples

    try:
        artist_examples = get_examples(tenant_id, artist=artist, dsn=dsn)
    except Exception:
        artist_examples = []
    has_examples = bool(artist_examples)

    opener_ex = next((e for e in artist_examples if not e.get("follow_up_to")), None)
    followup_ex = next((e for e in artist_examples if e.get("follow_up_to")), None)
    grounded_ids = [e["id"] for e in artist_examples]

    notes: list[str] = []
    if not has_examples:
        notes.append(
            f"No campaign examples on file for {artist}; generated from the studio's "
            "overall patterns (stated honestly to the operator)."
        )
    if attach_artwork:
        notes.append(_artwork_note(tenant_id, artist, dsn=dsn))

    pattern_summary = summarize_patterns(tenant_id, artist, dsn=dsn)

    drafts: list[GeneratedDraft] = []
    want_sms = "sms" in channels
    want_email = "email" in channels

    opener_ids = [opener_ex["id"]] if opener_ex else grounded_ids
    if want_sms:
        drafts.append(GeneratedDraft(
            channel="sms", role="opener",
            body=_opener_sms(artist, offer_price_usd, spots, payment_plan),
            grounded_example_ids=opener_ids, offer_price_usd=offer_price_usd,
            send_status=SMS_SEND_STATUS,
        ))
    if want_email:
        subj, body = _opener_email(artist, offer_price_usd, spots, payment_plan)
        drafts.append(GeneratedDraft(
            channel="email", role="opener", body=body, subject=subj,
            grounded_example_ids=opener_ids, offer_price_usd=offer_price_usd,
            send_status=EMAIL_SEND_STATUS,
        ))

    if follow_up:
        fu_ids = [followup_ex["id"]] if followup_ex else opener_ids
        if want_sms:
            drafts.append(GeneratedDraft(
                channel="sms", role="follow_up",
                body=_followup_sms(artist, offer_price_usd, spots),
                grounded_example_ids=fu_ids, offer_price_usd=offer_price_usd,
                send_status=SMS_SEND_STATUS,
            ))
        if want_email:
            subj, body = _followup_email(artist, offer_price_usd, spots)
            drafts.append(GeneratedDraft(
                channel="email", role="follow_up", body=body, subject=subj,
                grounded_example_ids=fu_ids, offer_price_usd=offer_price_usd,
                send_status=EMAIL_SEND_STATUS,
            ))

    return GeneratedCampaign(
        tenant_id=tenant_id, artist=artist, pattern_summary=pattern_summary,
        drafts=drafts, grounded_example_ids=grounded_ids, has_artist_examples=has_examples,
        offer_price_usd=offer_price_usd, notes=notes,
    )


def _artwork_note(tenant_id: str, artist: str, *, dsn: str | None = None) -> str:
    """Honest artwork status: a real asset on file, or an explicit flag that none is
    available (never a fabricated asset reference)."""
    try:
        from studio.artwork_select import list_artwork, select_artwork

        pick = select_artwork(list_artwork(tenant_id, artist, dsn=dsn))
    except Exception:
        pick = None
    if pick is not None:
        return f"Artwork on file for {artist} — attach on approval."
    return (
        f"Attachment requested but NO artwork asset is on file for {artist}; the opener "
        "goes without an image (never a fabricated attachment)."
    )


# --------------------------------------------------------------------------- #
# Staging — into the review queue, with example-grounding visible.
# --------------------------------------------------------------------------- #
def stage_campaign(
    campaign: GeneratedCampaign, *, run_id: str, dsn: str | None = None
) -> list[str]:
    """Stage every generated draft into the review queue (HELD for approval). The staged
    action's context carries the grounded example-ids + the send-path honesty, so the
    operator SEES which real examples each draft was built on. Idempotent per
    (run, channel, role). Never sends — SMS has no send path; test mode blocks sends."""
    from actions.store import record_pending_action

    staged: list[str] = []
    for i, d in enumerate(campaign.drafts):
        context = json.dumps({
            "generator": "campaign_generator",
            "artist": campaign.artist,
            "role": d.role,
            "grounded_example_ids": d.grounded_example_ids,
            "send_status": d.send_status,
            "no_send_path": d.channel == "sms",
            "offer_price_usd": d.offer_price_usd,
            "pattern_summary": campaign.pattern_summary,
            "notes": campaign.notes + d.notes,
        })
        action_id = record_pending_action(
            tenant_id=campaign.tenant_id, decision_id=None, type="campaign_message",
            channel=d.channel, worker="campaign_generator", target=None,
            draft=d.body, subject=d.subject, context=context,
            conf=None, threshold=None, esc_kind="approval_required",
            esc_label=f"{campaign.artist} campaign {d.role} ({d.channel}) — approval required"
            + (" — NO SMS SEND PATH (review only)" if d.channel == "sms" else ""),
            idempotency_key=f"{run_id}:{d.channel}:{d.role}:{i}",
            run_id=run_id, dsn=dsn,
        )
        staged.append(action_id)
    return staged
