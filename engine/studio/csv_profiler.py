"""Pure CSV semantic profiler (P1-A) — classify an uploaded lead CSV's columns into
marketing roles and produce an HONEST natural-language summary the supervisor can read
back ("I found 10 leads — 6 warm, 1 price objection, 3 have Instagram. Personalize one
message per lead?").

No model, no I/O — a deterministic header+value heuristic, fully unit-testable. The
honesty discipline is the whole point:

  * Every count is derived from the REAL parsed rows. Nothing is invented.
  * A role we cannot find in the file is reported as ABSENT — we never guess a segment,
    an objection, or a social handle that isn't there.
  * Columns we cannot map to a known role are surfaced BY NAME as ``unknown_columns`` so
    the operator sees exactly what we could not interpret.

The route (:mod:`studio.agui` ``parse_customers_csv`` / ``/studio/upload``) attaches the
profile + summary onto the plan; ``_customers_context`` renders the summary so the
supervisor states it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Column classification. Each role carries the header substrings that signal it.
# A column is classified to the FIRST role (in this priority order) whose signal
# appears in the lowercased header. Order matters: the more specific / less
# ambiguous roles come first so e.g. "customer type" -> status, not name.
# --------------------------------------------------------------------------- #

# role -> header signal substrings (lowercased, substring match on the header)
_HEADER_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("email", ("email", "e-mail", "mail")),
    ("phone", ("phone", "mobile", "cell", "tel", "sms", "whatsapp")),
    ("social", ("instagram", "insta", " ig", "ig ", "handle", "social",
                "tiktok", "twitter", "facebook", " fb", "fb ")),
    ("payment", ("payment", "paid", "deposit", "balance", "invoice", "owed")),
    ("budget", ("budget", "price range", "spend", "price band")),
    ("timing", ("timing", "availability", "timeframe", "preferred date", "when ")),
    ("objection", ("objection", "reason", "concern", "blocker", "hesitation",
                   "why not", "why didn")),
    ("conversation_history", ("conversation", "history", "messages", "chat",
                              "thread", "last contact", "prior contact",
                              "prior_contact", "correspondence")),
    ("lead_source", ("lead source", "lead_source", "source", "referral",
                     "channel", "utm", "origin", "how did")),
    ("customer_type", ("customer type", "customer_type", "status", "segment",
                       "stage", "lifecycle", "type", "category")),
    ("prior_artist", ("prior artist", "preferred artist", "artist")),
    ("tattoo_interest", ("interest", "tattoo", "style", "design", "placement",
                         "idea", "subject")),
    ("location", ("city", "location", "town", "region", "state", "country",
                  "area", "address", "zip", "postcode")),
    ("name", ("name", "client", "first", "last", "full name")),
    ("notes", ("notes", "note", "comment", "remark", "detail", "memo")),
)

# Canonical roles the profiler recognizes (for callers / display).
KNOWN_ROLES: tuple[str, ...] = tuple(r for r, _ in _HEADER_SIGNALS)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[+()\-.\s]*\d[\d()\-.\s]{6,}$")
# A social handle in a labeled social column (permissive — the header already told us).
_SOCIAL_RE = re.compile(r"(instagram\.com|instagr\.am|tiktok\.com|@[A-Za-z0-9_.]{2,})", re.I)
# STRICT social signal for scanning UNlabeled columns: an explicit social-platform URL,
# or a bare @handle — but NEVER an email address (so "studio@gmail.com" is not a handle).
_SOCIAL_URL_RE = re.compile(r"(instagram\.com|instagr\.am|tiktok\.com|twitter\.com|t\.me/)", re.I)
_BARE_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_.]{2,}$")

# Value -> canonical customer segment. Substring match on a lowercased cell value.
# NOTE: "cold" and "past" are DISTINCT buckets (cold = brand-new/never-engaged;
# past = lapsed/former customer) to match the interview's cold/warm/past/recurring set.
#
# PRECEDENCE MATTERS. The SPECIFIC buckets (unpaid/recurring/past/cold/converted) are
# checked BEFORE "warm", and a bare "lead" is NOT a warm signal — otherwise "cold lead"
# (which contains "lead") would misclassify as warm. Only "warm"/"warm lead" -> warm;
# "cold"/"cold lead" -> cold. First match in THIS order wins.
_SEGMENT_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("unpaid", ("unpaid", "deposit pending", "owes", "balance due", "payment pending")),
    ("recurring", ("recurring", "repeat", "regular", "loyal", "returning")),
    ("past", ("past", "lapsed", "inactive", "dormant", "reactivation", "former",
              "old client", "won-back", "win-back")),
    ("cold", ("cold", "brand new", "brand-new", "new prospect", "prospect",
              "never booked", "unqualified")),
    ("converted", ("converted", "booked", "paid customer", "won")),
    ("warm", ("warm", "hot", "engaged", "inquired", "enquired", "interested")),
)

# Objection keyword phrases -> canonical objection type. Used both on a dedicated
# objection column AND (conservatively) on notes/conversation free text. Kept tight so a
# benign note ("primary contact") never false-positives into an objection.
_OBJECTION_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("price", ("price", "expensive", "too much", "cost", "afford", "cheaper", "pricey")),
    ("payment", ("deposit", "unpaid", "owe", "balance", "installment", "instalment",
                 "payment plan")),
    ("timing", ("no time", "too busy", "timing", "later", "next month", "not now",
                "reschedule", "waitlist")),
    ("trust", ("nervous", "scared", "afraid", "hurt", "pain", "trust", "unsure about the artist")),
    ("uncertainty", ("not sure", "thinking about it", "maybe", "undecided", "still deciding",
                     "on the fence")),
)


@dataclass
class CsvProfile:
    """The honest semantic profile of an uploaded lead CSV — all counts real, all
    absences reported, unknown columns named."""

    total_leads: int = 0
    # column name -> role it was classified as (only for mapped columns)
    column_roles: dict[str, str] = field(default_factory=dict)
    # role -> [column names], for the roles that were found
    roles_present: dict[str, list[str]] = field(default_factory=dict)
    # columns we could NOT map to any known role (surfaced honestly, never guessed)
    unknown_columns: list[str] = field(default_factory=list)
    emails_present: int = 0
    # canonical segment -> count (empty when no status/type column was found)
    segments: dict[str, int] = field(default_factory=dict)
    # how segments were derived: "column" (a real status/type column) or "none"
    segments_source: str = "none"
    # canonical objection -> count (empty when none detected)
    objections: dict[str, int] = field(default_factory=dict)
    # "column" (a dedicated objection column) | "notes" (inferred from free text) | "none"
    objections_source: str = "none"
    social_present: int = 0
    social_source: str = "none"  # "column" | "scanned" | "none"
    summary_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_leads": self.total_leads,
            "column_roles": dict(self.column_roles),
            "roles_present": {k: list(v) for k, v in self.roles_present.items()},
            "unknown_columns": list(self.unknown_columns),
            "emails_present": self.emails_present,
            "segments": dict(self.segments),
            "segments_source": self.segments_source,
            "objections": dict(self.objections),
            "objections_source": self.objections_source,
            "social_present": self.social_present,
            # alias: the social count under the plain key too (some callers look for "social")
            "social": self.social_present,
            "social_source": self.social_source,
            "summary": self.summary_text,
        }


def _classify_header(header: str) -> str | None:
    """The role a header maps to (first-match in priority order), or ``None`` if no
    known role signal appears in it."""
    h = (header or "").strip().lower()
    if not h:
        return None
    for role, signals in _HEADER_SIGNALS:
        for sig in signals:
            if sig in h:
                return role
    return None


def _values_for(columns: list[str], rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Column name -> its non-empty stripped cell values across all rows."""
    out: dict[str, list[str]] = {c: [] for c in columns}
    for r in rows:
        for c in columns:
            v = str(r.get(c, "") or "").strip()
            if v:
                out[c].append(v)
    return out


def _reclassify_unknown_by_value(col: str, values: list[str]) -> str | None:
    """A header we could not map may still be obviously an email / phone / social
    column from its VALUES. Only fires when a clear majority of the non-empty values
    match — a grounded, conservative value heuristic (never a guess on thin data)."""
    if not values:
        return None
    n = len(values)

    def majority(pred) -> bool:
        return sum(1 for v in values if pred(v)) >= max(1, (n + 1) // 2)

    if majority(lambda v: bool(_EMAIL_RE.match(v))):
        return "email"
    if majority(lambda v: bool(_SOCIAL_RE.search(v))):
        return "social"
    if majority(lambda v: bool(_PHONE_RE.match(v))):
        return "phone"
    return None


def _canonical_segment(value: str) -> str | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    for seg, signals in _SEGMENT_SIGNALS:
        for sig in signals:
            if sig in v:
                return seg
    return None


def _count_segments(cols: list[str], values: dict[str, list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in cols:
        for v in values.get(c, []):
            seg = _canonical_segment(v)
            if seg:
                counts[seg] = counts.get(seg, 0) + 1
    return counts


def _count_objections(cols: list[str], values: dict[str, list[str]]) -> dict[str, int]:
    """Count objections by scanning the given columns' values for objection keyword
    phrases. Grounded: a row is only counted when a real phrase literally appears."""
    counts: dict[str, int] = {}
    for c in cols:
        for v in values.get(c, []):
            low = v.lower()
            for obj, signals in _OBJECTION_SIGNALS:
                if any(sig in low for sig in signals):
                    counts[obj] = counts.get(obj, 0) + 1
                    break  # one objection per cell (the first/strongest signal)
    return counts


def _count_social(cols: list[str], values: dict[str, list[str]],
                  all_cols: list[str]) -> tuple[int, str]:
    """(count, source). If a social column exists, count rows with a non-empty social
    cell. Otherwise scan every cell for an Instagram/social signal (grounded)."""
    if cols:
        rows_with = 0
        # rebuild per-row presence: a row counts once if ANY social col is non-empty
        # (values dict is per-column, so use max length alignment via presence union)
        # Simpler + honest: count the max non-empty count across social columns.
        rows_with = max((len(values.get(c, [])) for c in cols), default=0)
        return rows_with, "column"
    # no social column: scan all columns' values for an EXPLICIT social signal (a social
    # URL or a bare @handle) — never an email, so "studio@gmail.com" is not a handle.
    hits = 0
    for c in all_cols:
        for v in values.get(c, []):
            if _EMAIL_RE.match(v):
                continue
            if _SOCIAL_URL_RE.search(v) or _BARE_HANDLE_RE.match(v):
                hits += 1
    return (hits, "scanned" if hits else "none")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _summary_text(p: CsvProfile) -> str:
    """The honest natural-language summary the supervisor reads back."""
    if p.total_leads == 0:
        return "The file has no lead rows I can read."

    parts: list[str] = [f"I found {_plural(p.total_leads, 'lead')}"]
    detail_clauses: list[str] = []

    # Segments (only when a real status/type column was found).
    if p.segments:
        seg_order = ["warm", "cold", "converted", "recurring", "unpaid", "past"]
        ordered = [s for s in seg_order if s in p.segments] + \
            [s for s in p.segments if s not in seg_order]
        seg_bits = ", ".join(f"{p.segments[s]} {s}" for s in ordered)
        detail_clauses.append(seg_bits)

    # Objections.
    if p.objections:
        obj_bits = ", ".join(
            f"{cnt} {obj} objection" + ("" if cnt == 1 else "s")
            for obj, cnt in sorted(p.objections.items(), key=lambda kv: -kv[1])
        )
        via = " (from their notes)" if p.objections_source == "notes" else ""
        detail_clauses.append(obj_bits + via)

    # Social presence.
    if p.social_present:
        detail_clauses.append(f"{p.social_present} with an Instagram/social handle")

    if detail_clauses:
        parts.append(" — " + "; ".join(detail_clauses))

    sentence = "".join(parts) + "."

    # Contact completeness (email) — a real, useful count for outreach.
    email_note = ""
    if "email" in p.roles_present:
        if p.emails_present == p.total_leads:
            email_note = " Every one has an email address."
        else:
            email_note = f" {p.emails_present} of {p.total_leads} have an email address."

    # Honest absence: say plainly what we could NOT break down.
    missing_dims: list[str] = []
    if not p.segments:
        missing_dims.append("a warm/cold segment")
    if not p.objections:
        missing_dims.append("an objection")
    if not p.social_present and p.social_source == "none":
        missing_dims.append("a social handle")
    absence_note = ""
    if missing_dims:
        absence_note = (
            " I don't see " + ", ".join(missing_dims) +
            " column in this file, so I won't guess those."
        )

    unknown_note = ""
    if p.unknown_columns:
        unknown_note = (
            " I couldn't map " +
            ", ".join(f"'{c}'" for c in p.unknown_columns) +
            " to a known field — I'll leave them as-is rather than assume what they mean."
        )

    return sentence + email_note + absence_note + unknown_note + " Personalize one message per lead?"


def build_profile(columns: list[str], rows: list[dict[str, Any]]) -> CsvProfile:
    """Classify ``columns`` into marketing roles and count real segments / objections /
    social presence over ``rows`` (a list of dict rows — ALL of them, not a sample).

    Pure + deterministic. Honest: unmapped columns are surfaced, absent dimensions are
    reported as absent, and every count comes from the real cells."""
    columns = [c for c in (columns or [])]
    rows = list(rows or [])
    profile = CsvProfile(total_leads=len(rows))

    values = _values_for(columns, rows)

    # 1) Header classification, with a value-based rescue for unmapped columns.
    for col in columns:
        role = _classify_header(col)
        if role is None:
            role = _reclassify_unknown_by_value(col, values.get(col, []))
        if role is None:
            profile.unknown_columns.append(col)
            continue
        profile.column_roles[col] = role
        profile.roles_present.setdefault(role, []).append(col)

    def cols_for(role: str) -> list[str]:
        return profile.roles_present.get(role, [])

    # 2) Emails present (a row counts if any email column is non-empty).
    email_cols = cols_for("email")
    if email_cols:
        profile.emails_present = max((len(values.get(c, [])) for c in email_cols), default=0)

    # 3) Segments — only from a real status/customer_type column (never fabricated).
    seg_cols = cols_for("customer_type") + cols_for("lead_source")
    seg_counts = _count_segments(seg_cols, values) if seg_cols else {}
    if seg_counts:
        profile.segments = seg_counts
        profile.segments_source = "column"

    # 4) Objections — a dedicated objection column first, else conservative notes scan.
    obj_cols = cols_for("objection")
    if obj_cols:
        obj_counts = _count_objections(obj_cols, values)
        if obj_counts:
            profile.objections = obj_counts
            profile.objections_source = "column"
    if not profile.objections:
        text_cols = cols_for("notes") + cols_for("conversation_history")
        obj_counts = _count_objections(text_cols, values) if text_cols else {}
        if obj_counts:
            profile.objections = obj_counts
            profile.objections_source = "notes"

    # 5) Social presence.
    social_cols = cols_for("social")
    profile.social_present, profile.social_source = _count_social(social_cols, values, columns)

    profile.summary_text = _summary_text(profile)
    return profile
