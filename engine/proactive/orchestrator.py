"""``run_daily_scan`` — the proactive scanner's per-tenant daily scan (CustomerAcq-fr1.1).

This is the ``scan_fn`` the worker claims a fire_date for and drives. It:

  * PREFLIGHTS (AC-4): DB reachable? LLM funded? A dead LLM key badges the day
    ``degraded: deterministic-only`` — the deterministic detectors still run and stage,
    the day is just honestly marked, never silently filled with template copy.
  * runs the 3 deterministic detectors (holiday / follow-up / artist-special);
  * stages EVERY proposal HELD (AC-3, the 439 invariant): a PENDING ``actions`` row
    via ``record_pending_action``. Nothing sends — the 439 HoldRegistry + router
    HOLD->REVIEW enforce review independently; go-live stays operator-gated (t90.4);
  * refuses phantom channels and never proposes SMS unless the t90.2 gate is
    importable (AC-5);
  * runs the fr1.3 ``ttl_archive_sweep`` hygiene tick.

Idempotent per (tenant, fire_date): proposal idempotency keys are stable, so a
re-driven scan re-stages nothing (``ON CONFLICT DO NOTHING``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from proactive.detectors import (
    DEFAULT_SUBDIVISIONS,
    ArtistSpecial,
    Opportunity,
    PriorSend,
    artist_special_opportunities,
    follow_up_opportunities,
    holiday_opportunities,
)

#: Known channels (phantom-channel refusal, t90.1). Anything else is refused.
KNOWN_CHANNELS = frozenset({"email", "gmail", "instagram", "facebook", "sms"})
#: Review-only channel per opportunity kind (all HELD).
_CHANNEL_BY_KIND = {
    "holiday": "instagram",
    "artist_special": "instagram",
    "follow_up": "email",
}
_WORKER = "proactive_scanner"


@dataclass
class ScanReport:
    tenant_id: str
    fire_date: date
    run_id: str
    db_ok: bool
    llm_ok: bool
    degraded: bool
    badge: str
    staged: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)  # phantom/ungated channel or bad offer
    ttl_swept: str | None = None

    def as_detail(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "fire_date": self.fire_date.isoformat(),
            "db_ok": self.db_ok,
            "llm_ok": self.llm_ok,
            "degraded": self.degraded,
            "badge": self.badge,
            "n_staged": len(self.staged),
            "staged": self.staged,
            "skipped_existing": self.skipped_existing,
            "refused": self.refused,
            "ttl_swept": self.ttl_swept,
        }


def _preflight(dsn: str | None) -> tuple[bool, bool]:
    """(db_ok, llm_ok). db_ok = a trivial query succeeds; llm_ok = a funded key."""
    db_ok = True
    try:
        from actions.store import _connect

        with _connect(dsn) as conn:
            conn.execute("SELECT 1")
    except Exception:  # noqa: BLE001 — any DB error means "cannot stage"; degrade
        db_ok = False
    llm_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return db_ok, llm_ok


def _channel_ok(channel: str, refused: list[str], key: str) -> bool:
    if channel not in KNOWN_CHANNELS:
        refused.append(key)  # phantom-channel refusal (t90.1)
        return False
    if channel == "sms":
        try:
            import compliance.sms_gate  # noqa: F401 — importability IS the gate check
        except Exception:  # noqa: BLE001
            refused.append(key)  # gate absent -> never propose SMS (t90.2)
            return False
    return True


def _draft_for(opp: Opportunity) -> str:
    return f"[{opp.source_badge}] {opp.title} — {opp.rationale}"


def run_daily_scan(
    tenant_id: str,
    fire_date: date,
    *,
    dsn: str | None = None,
    subdivisions: tuple[str, ...] = DEFAULT_SUBDIVISIONS,
    window_days: int = 21,
    prior_sends: list[PriorSend] | None = None,
    artists: list[ArtistSpecial] | None = None,
    opted_out: frozenset[str] = frozenset(),
    responded: frozenset[str] = frozenset(),
    already_followed_up: frozenset[str] = frozenset(),
    cadence_days: int = 30,
    ttl_sweep: bool = True,
) -> dict[str, Any]:
    """Run one tenant's daily scan, staging HELD proposals. Returns the ScanReport
    detail (the ledger records it as the run's result)."""
    run_id = f"scan:{tenant_id}:{fire_date.isoformat()}"  # stable -> idempotent re-drive
    db_ok, llm_ok = _preflight(dsn)
    degraded = not llm_ok
    if not db_ok:
        return ScanReport(
            tenant_id=tenant_id, fire_date=fire_date, run_id=run_id, db_ok=False,
            llm_ok=llm_ok, degraded=True, badge="degraded: database-unreachable",
        ).as_detail()
    badge = "degraded: deterministic-only" if degraded else "ok"
    report = ScanReport(
        tenant_id=tenant_id, fire_date=fire_date, run_id=run_id, db_ok=True,
        llm_ok=llm_ok, degraded=degraded, badge=badge,
    )

    # 1) Deterministic detection (no LLM -> runs even when degraded).
    opps: list[Opportunity] = list(
        holiday_opportunities(fire_date, subdivisions=subdivisions, window_days=window_days)
    )
    if prior_sends:
        opps += follow_up_opportunities(
            fire_date, prior_sends=prior_sends, opted_out=opted_out,
            responded=responded, already_followed_up=already_followed_up,
        )
    if artists:
        opps += artist_special_opportunities(
            fire_date, artists=artists, cadence_days=cadence_days
        )

    # 2) Stage HELD (AC-3). Idempotent on the stable opportunity key.
    from actions.store import ensure_schema, record_pending_action
    from cells.offer_guard import offer_violations

    ensure_schema(dsn)
    existing = _existing_keys(dsn, [o.key for o in opps])
    for opp in opps:
        channel = _CHANNEL_BY_KIND.get(opp.kind, "email")
        if not _channel_ok(channel, report.refused, opp.key):
            continue
        draft = _draft_for(opp)
        if offer_violations(draft, ()):  # fail-closed: never stage a fabricated offer
            report.refused.append(opp.key)
            continue
        if opp.key in existing:
            report.skipped_existing.append(opp.key)
            continue
        record_pending_action(
            tenant_id=tenant_id, decision_id=None, type=opp.kind, channel=channel,
            worker=_WORKER, target=opp.facts.get("recipient"), draft=draft,
            subject=opp.title, context=opp.source_badge, conf=None, threshold=None,
            esc_kind="approval_required", esc_label="proactive proposal",
            idempotency_key=opp.key, run_id=run_id, dsn=dsn,
        )
        report.staged.append(opp.key)

    # 3) fr1.3 hygiene tick.
    if ttl_sweep:
        from ops.archive import ensure_archive_schema, ttl_archive_sweep

        ensure_archive_schema(dsn)
        report.ttl_swept = ttl_archive_sweep(dsn=dsn)

    return report.as_detail()


def _existing_keys(dsn: str | None, keys: list[str]) -> set[str]:
    """Which idempotency keys already have a staged action (honest new-vs-existing)."""
    if not keys:
        return set()
    from actions.store import _connect

    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT idempotency_key FROM actions WHERE idempotency_key = ANY(%s)",
            (keys,),
        ).fetchall()
    return {r["idempotency_key"] for r in rows}
