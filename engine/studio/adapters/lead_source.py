"""Lead source adapters — normalized :class:`Lead` records from any backing system.

``CsvLeadSource`` works now (uploaded CSV -> normalized Lead, carrying the extended
tattoo fields per ADR §4.6). ``StribeConversationSource`` / ``MiniAppCrmSource`` are
honest stubs that raise :class:`~studio.adapters.NotConfiguredError` until the client
APIs exist — they NEVER fabricate a lead.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable

from studio.adapters import NotConfiguredError


@dataclass
class Lead:
    """The single normalized lead the graph nodes consume, regardless of source.

    Only ``name``/``email`` are commonly present; every tattoo-specific field is optional
    and honestly empty when the source did not provide it (never defaulted to a fake)."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    ig_handle: str | None = None
    city: str | None = None
    state: str | None = None
    interests: list[str] = field(default_factory=list)
    notes: str | None = None
    # Extended tattoo-agency fields (ADR §4.6) — the shape Stribe/Mini-App will populate.
    shop: str | None = None
    artist: str | None = None
    lead_stage: str | None = None
    customer_type: str | None = None
    payment_status: str | None = None
    conversation_ref: str | None = None
    # Free-form carry-through for any other real column (never dropped silently).
    extra: dict[str, Any] = field(default_factory=dict)

    def as_upsert_row(self) -> dict[str, Any]:
        """The row shape ``customer_research.upsert_lead`` accepts (real values only)."""
        row: dict[str, Any] = {}
        if self.name:
            row["name"] = self.name
        if self.email:
            row["email"] = self.email
        if self.city or self.state:
            row["location"] = ", ".join(x for x in (self.city, self.state) if x)
        if self.interests:
            row["interests"] = "; ".join(self.interests)
        if self.notes:
            row["notes"] = self.notes
        for k in ("artist", "shop", "lead_stage", "customer_type", "payment_status", "ig_handle"):
            v = getattr(self, k)
            if v:
                row[k] = v
        return row


@runtime_checkable
class LeadSourceProtocol(Protocol):
    """Yields normalized :class:`Lead` records. ``name`` identifies the source for the UI."""

    name: str

    def leads(self) -> Iterator[Lead]:
        ...


# --------------------------------------------------------------------------- #
# Column normalization — map many real header spellings onto the Lead fields.
# --------------------------------------------------------------------------- #
_COLUMN_ALIASES: dict[str, str] = {
    "name": "name", "full name": "name", "customer": "name", "client": "name",
    "email": "email", "e-mail": "email", "email address": "email",
    "phone": "phone", "mobile": "phone", "number": "phone",
    "ig": "ig_handle", "instagram": "ig_handle", "ig handle": "ig_handle", "handle": "ig_handle",
    "city": "city", "location": "location", "town": "city",
    "state": "state", "region": "state",
    "interests": "interests", "interest": "interests", "styles": "interests", "style": "interests",
    "notes": "notes", "note": "notes", "comment": "notes", "comments": "notes",
    "artist": "artist", "preferred artist": "artist",
    "shop": "shop", "studio": "shop",
    "lead stage": "lead_stage", "stage": "lead_stage",
    "customer type": "customer_type", "type": "customer_type", "segment": "customer_type",
    "payment status": "payment_status", "payment": "payment_status",
    "conversation": "conversation_ref", "conversation ref": "conversation_ref",
    "transcript": "conversation_ref",
}


def _split_location(location: str) -> tuple[str | None, str | None]:
    parts = [p.strip() for p in str(location).split(",")]
    if len(parts) >= 2:
        return parts[0] or None, parts[1] or None
    return (parts[0] or None), None


def _row_to_lead(record: dict[str, str]) -> Lead:
    """Map one normalized-key record to a :class:`Lead`. Unknown columns are preserved in
    ``extra`` (never silently dropped)."""
    lead = Lead()
    extra: dict[str, Any] = {}
    for raw_key, raw_val in record.items():
        val = (raw_val or "").strip()
        if not val:
            continue
        norm_key = (raw_key or "").strip().lower()
        key = _COLUMN_ALIASES.get(norm_key) or _COLUMN_ALIASES.get(norm_key.replace("_", " "))
        if key == "location":
            lead.city, lead.state = _split_location(val)
        elif key == "interests":
            lead.interests = [s.strip() for s in val.replace(",", ";").split(";") if s.strip()]
        elif key in (
            "name", "email", "phone", "ig_handle", "city", "state", "notes",
            "artist", "shop", "lead_stage", "customer_type", "payment_status",
            "conversation_ref",
        ):
            setattr(lead, key, val)
        else:
            extra[raw_key] = val
    lead.extra = extra
    return lead


class CsvLeadSource:
    """Normalized leads from an uploaded CSV (works now). Tolerant header mapping; any
    unrecognized column is carried in ``Lead.extra`` rather than dropped."""

    name = "uploaded CSV"

    def __init__(self, content: str) -> None:
        self._content = content or ""

    @classmethod
    def from_rows(cls, rows: list[dict[str, str]]) -> "CsvLeadSource":
        """Build directly from already-parsed rows (bypasses CSV text parsing)."""
        obj = cls("")
        obj._rows = rows  # type: ignore[attr-defined]
        return obj

    def leads(self) -> Iterator[Lead]:
        rows = getattr(self, "_rows", None)
        if rows is None:
            rows = self._parse()
        for r in rows:
            lead = _row_to_lead(r)
            if lead.name or lead.email:  # a row with no identity is not a lead
                yield lead

    def _parse(self) -> list[dict[str, str]]:
        text = self._content.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not text.strip():
            return []
        reader = csv.DictReader(io.StringIO(text))
        return [ {k: (v or "") for k, v in row.items()} for row in reader ]


class StribeConversationSource:
    """STUB: warm/open leads + threads from the Stribe conversational platform.

    Not connected yet — the client's Stribe APIs are not available. Raises an honest
    :class:`NotConfiguredError` rather than fabricating leads. Swap in the real impl when
    the APIs land; the normalized :class:`Lead` contract does not change."""

    name = "Stribe (not connected)"

    def leads(self) -> Iterator[Lead]:
        raise NotConfiguredError(
            "Stribe is not connected yet — upload your leads as a CSV for now. "
            "Stribe conversation ingest will populate the same lead fields once its API "
            "is available."
        )


class MiniAppCrmSource:
    """STUB: converted / booked / recurring / past customers from the Mini-App CRM.

    Not connected yet — raises :class:`NotConfiguredError`. Never fabricates a customer."""

    name = "Mini-App CRM (not connected)"

    def leads(self) -> Iterator[Lead]:
        raise NotConfiguredError(
            "The Mini-App CRM is not connected yet — upload your customers as a CSV for "
            "now. The CRM will populate the same lead fields once its API is available."
        )
