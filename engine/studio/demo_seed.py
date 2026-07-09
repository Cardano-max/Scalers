"""Seed the tlv.6 dummy demo tenant (demo_studio / "Copper Fox Tattoo") from the
fictional rich-persona CSV that writer ships (demo-data/demo_studio/customers.csv).

The base importer (client_import.parse_customers_csv) only ingests name/email/phone
and REPORTS the rich columns as unknown — it does not seed personas. This seeder is
writer's confirmed option A: it ingests the ONE rich CSV
(name,email,phone,interests,last_visit,objection,preferred_artist,notes) and seeds
each persona into ``customers`` (via the idempotent ``upsert_lead``) PLUS a short
grounded conversation carrying the persona's own objection (via ``upsert_conversation``),
so the strategist's win-back proposals are grounded in real persona memory — never
fabricated. 100% fictional data; nothing sends.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

#: Default location of writer's demo CSV (PR #116). Overridable for tests.
DEFAULT_CSV = (
    Path(__file__).resolve().parents[2] / "demo-data" / "demo_studio" / "customers.csv"
)
DEMO_TENANT = "demo_studio"


@dataclass(frozen=True)
class Persona:
    name: str
    email: str
    phone: str | None
    interests: list[str] = field(default_factory=list)
    last_visit: str | None = None  # ISO yyyy-mm-dd; the win-back lapse signal
    objection: str | None = None
    preferred_artist: str | None = None
    notes: str | None = None

    def is_lapsed(self, *, today: date, days: int = 90) -> bool:
        """A win-back candidate: last visit older than ``days`` (default 90)."""
        if not self.last_visit:
            return False
        try:
            lv = date.fromisoformat(self.last_visit)
        except ValueError:
            return False
        return (today - lv).days >= days


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.replace(",", ";").split(";") if s.strip()]


def parse_demo_personas(csv_path: Path | str = DEFAULT_CSV) -> list[Persona]:
    """Parse the rich demo CSV into Personas (PURE — no DB). Rows without an email
    (the upsert key) are skipped."""
    path = Path(csv_path)
    personas: list[Persona] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            email = (raw.get("email") or "").strip().lower()
            if not email:
                continue
            personas.append(
                Persona(
                    name=(raw.get("name") or "").strip(),
                    email=email,
                    phone=(raw.get("phone") or "").strip() or None,
                    interests=_split(raw.get("interests")),
                    last_visit=(raw.get("last_visit") or "").strip() or None,
                    objection=(raw.get("objection") or "").strip() or None,
                    preferred_artist=(raw.get("preferred_artist") or "").strip() or None,
                    notes=(raw.get("notes") or "").strip() or None,
                )
            )
    return personas


def _objection_turns(persona: Persona) -> list[dict[str, str]]:
    """A minimal grounded exchange: the customer voices THEIR objection (verbatim from
    the persona), the studio acknowledges. The objection text is authored persona
    ground-truth, so the strategist grounds the win-back angle in a real signal."""
    if not persona.objection:
        return []
    return [
        {"speaker": "customer", "text": persona.objection},
        {
            "speaker": "studio",
            "text": "Thanks for letting us know — whenever the timing feels right, we'd love to pick your piece back up.",
        },
    ]


def seed_demo_studio(
    tenant_id: str = DEMO_TENANT,
    *,
    csv_path: Path | str = DEFAULT_CSV,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Idempotently seed the demo personas + their objection conversations. Best-effort
    per persona so one bad row never aborts the seed. Returns a summary. Nothing sends."""
    from studio.conversations import upsert_conversation
    from studio.customer_research import upsert_lead

    personas = parse_demo_personas(csv_path)
    customer_ids: list[str] = []
    conversations = 0
    errors: list[dict[str, str]] = []
    for p in personas:
        try:
            row = {
                "name": p.name,
                "email": p.email,
                "phone": p.phone or "",
                "interests": "; ".join(p.interests),
                "artist": p.preferred_artist or "",
                "notes": _notes_with_last_visit(p),
                "customer_type": "lapsed",
                "lead_stage": "lapsed",
            }
            res = upsert_lead(tenant_id, row, dsn=dsn)
            cid = res["customer_id"]
            customer_ids.append(cid)
            turns = _objection_turns(p)
            if turns:
                upsert_conversation(
                    tenant_id, cid, turns, channel="email", source="demo_seed",
                    campaign_message=None, dsn=dsn,
                )
                conversations += 1
        except Exception as exc:  # noqa: BLE001 — best-effort seed, surface per-row errors
            errors.append({"email": p.email, "error": repr(exc)})
    return {
        "tenant_id": tenant_id,
        "personas": len(personas),
        "customer_ids": customer_ids,
        "conversations": conversations,
        "errors": errors,
    }


def _notes_with_last_visit(p: Persona) -> str:
    """Fold the last-visit date into notes so the lapse is visible even where the
    schema has no dedicated column, without dropping writer's note content."""
    parts = [x for x in (p.notes, f"last visit {p.last_visit}" if p.last_visit else None) if x]
    return " — ".join(parts)


if __name__ == "__main__":  # pragma: no cover — runbook entry (python -m studio.demo_seed)
    import json
    import os

    out = seed_demo_studio(os.environ.get("DEMO_TENANT_ID", DEMO_TENANT))
    print(json.dumps(out, indent=2))
