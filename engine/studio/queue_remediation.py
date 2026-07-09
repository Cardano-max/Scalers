"""Remediate ALREADY-STAGED foreign-identity fabrications in the review queue
(CustomerAcq-wwy.7 — the generation-time-vs-existing-rows gap).

The wwy.7 guards stop NEW fabrications at staging. They do nothing for rows staged
BEFORE the fix: the live smoking gun left real "it's Rae from Ladies First" drafts
sitting PENDING against real skindesign recipients. A content-safety fix is not real
until the queue those drafts already poisoned is cleaned.

This is the reusable, honest, tenant-scoped sweep that closes that gap. It runs the
SAME deterministic :func:`cells.identity_guard.foreign_identity_violations` net over
existing staged rows and QUARANTINES the violators — transitioning them to the
existing terminal ``rejected`` status (the designed operator-declined transition), so
the exactly-once send claim (``actions.publish.claim_for_send`` requires
``status='pending'``) can never pick them up. The concrete reason is recorded in
``last_error`` for the audit trail.

Honesty / safety invariants:
* **Precise, not aggressive** — only FOREIGN tenant identity (another studio's name /
  handle) is quarantined. A tenant naming ITSELF is legitimate and untouched; a real
  grounded win-back draft is untouched. No per-lead facts are needed, so the sweep is
  exact (the identity net is facts-free).
* **Tenant-scoped** — :func:`ops.tenant_guard.assert_tenant_writable` gates the write,
  and every query is filtered by ``tenant_id``; a sweep for tenant A can never touch B.
* **Dry-run by default** — ``apply=False`` reports what WOULD be quarantined and writes
  nothing. Only ``apply=True`` transitions rows.
* **Idempotent** — it only considers rows in ``target_statuses`` (default pending +
  failed); an already-``rejected`` row is not re-touched.
* **No fakes** — the report is exactly what is in the DB; nothing is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from actions.store import get_action, list_actions, update_status
from cells.identity_guard import foreign_identity_violations
from ops.tenant_guard import assert_tenant_writable

# Statuses a fabrication can sit in and still be a risk: PENDING (one bulk-approve from
# sending) or FAILED (a fabrication that must never be retried). SENT is terminal and
# out of scope (already delivered — a different, escalation-only problem).
_DEFAULT_TARGET_STATUSES = ("pending", "failed")


@dataclass
class RemediationReport:
    """The honest result of a sweep — exactly what was found / changed."""

    tenant_id: str
    scanned: int = 0
    applied: bool = False
    flagged: list[dict[str, Any]] = field(default_factory=list)      # would-quarantine
    quarantined: list[dict[str, Any]] = field(default_factory=list)  # actually rejected
    skipped: list[dict[str, Any]] = field(default_factory=list)      # status moved under us

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "scanned": self.scanned,
            "applied": self.applied,
            "flagged": self.flagged,
            "quarantined": self.quarantined,
            "skipped": self.skipped,
            "n_flagged": len(self.flagged),
            "n_quarantined": len(self.quarantined),
        }


def _draft_text(row: Any) -> str:
    return f"{getattr(row, 'subject', None) or ''}\n{getattr(row, 'draft', None) or ''}"


def sweep_foreign_identity(
    tenant_id: str,
    *,
    dsn: str | None = None,
    target_statuses: tuple[str, ...] = _DEFAULT_TARGET_STATUSES,
    apply: bool = False,
) -> RemediationReport:
    """Scan ``tenant_id``'s staged rows for FOREIGN tenant identity and, when
    ``apply``, quarantine each violator (``status -> 'rejected'``, reason in
    ``last_error``). Returns a :class:`RemediationReport`.

    ``apply=False`` (default) is a pure dry run — nothing is written. Only a real
    foreign-identity match is flagged; a tenant's own identity and honest copy are
    never touched. Rejecting only protects a row that is still in ``target_statuses``
    at write time (re-checked per row), so a concurrently-claimed row is skipped rather
    than clobbered."""
    if apply:
        # Refuse to write to a protected/unknown tenant (mirrors the archive sweep).
        assert_tenant_writable(tenant_id)

    report = RemediationReport(tenant_id=tenant_id, applied=apply)
    rows: list[Any] = []
    for status in target_statuses:
        rows.extend(list_actions(tenant_id, status=status, dsn=dsn))
    report.scanned = len(rows)

    for row in rows:
        violations = foreign_identity_violations(_draft_text(row), tenant_id)
        if not violations:
            continue
        entry = {
            "id": row.id,
            "type": getattr(row, "type", None),
            "channel": row.channel,
            "status": row.status,
            "target": row.target,
            "is_seeded": getattr(row, "is_seeded", None),
            "reason": violations[0],
        }
        report.flagged.append(entry)
        if not apply:
            continue

        # Re-check the CURRENT status right before writing so we never clobber a row
        # that was claimed for send between the scan and now (TOCTOU-safe enough for a
        # single-operator queue; skipped rows are reported honestly).
        current = get_action(row.id, dsn=dsn)
        if current is None or current.status not in target_statuses:
            report.skipped.append({**entry, "current_status":
                                   getattr(current, "status", None)})
            continue
        update_status(
            row.id, "rejected",
            dsn=dsn,
            last_error=f"quarantined (wwy.7 foreign-identity remediation): {violations[0]}",
        )
        report.quarantined.append(entry)

    return report
