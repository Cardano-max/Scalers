"""Suppression-first gate (bead 1mk.7, spec §5: "suppression checked before every send").

The do-not-contact list is the FIRST gate — checked before verification, before
any sequence is built, before any touch. A suppressed prospect is skipped, full
stop. Loading the list from the tenant source (``minio://…`` per the pack's
``[suppression]``) is an eng/ops seam; this module is the deterministic check and
takes the loaded set in-memory so it is hermetic + testable.

Match is on the opaque prospect_ref (sha256), and also on normalized email +
domain, so a list can suppress an exact address or a whole domain.
"""

from __future__ import annotations

from collections.abc import Iterable

from outreach.schema import Prospect, SuppressionResult, prospect_ref


class SuppressionGate:
    """In-memory do-not-contact check. Suppress by exact email, by domain, or by
    pre-hashed ref (so a list can carry refs without raw addresses)."""

    def __init__(
        self,
        *,
        emails: Iterable[str] = (),
        domains: Iterable[str] = (),
        refs: Iterable[str] = (),
    ) -> None:
        self._emails = {e.strip().lower() for e in emails if e.strip()}
        self._domains = {d.strip().lower().lstrip("@") for d in domains if d.strip()}
        self._refs = set(refs)

    @classmethod
    def from_ledger(cls, *, tenant_id: str, dsn: str | None = None) -> "SuppressionGate":
        """Build the email gate from the cross-channel suppression ledger
        (CustomerAcq-t90.3): every ``email`` or ``'all'``-channel revocation —
        web-form and verbal revocations included — suppresses the email path
        too. One ledger, every channel."""
        from suppression.ledger import _connect  # lazy: this module stays DB-free by default

        with _connect(dsn) as conn:
            rows = conn.execute(
                "SELECT DISTINCT identifier FROM suppression_ledger"
                " WHERE tenant_id = %s AND (channel = 'email' OR channel = 'all')",
                (tenant_id,),
            ).fetchall()
        emails = [r["identifier"] for r in rows if "@" in r["identifier"]]
        return cls(emails=emails)

    @classmethod
    def from_rows(cls, rows: Iterable[str]) -> "SuppressionGate":
        """Build from raw list rows. A row starting with '@' is a domain rule; a
        32+ hex row is treated as a ref; anything with '@' inside is an email."""
        emails, domains, refs = [], [], []
        for raw in rows:
            row = raw.strip()
            if not row or row.startswith("#"):
                continue
            if row.startswith("@"):
                domains.append(row)
            elif "@" in row:
                emails.append(row)
            else:
                refs.append(row)  # opaque ref
        return cls(emails=emails, domains=domains, refs=refs)

    def check(self, prospect: Prospect) -> SuppressionResult:
        email = prospect.email.strip().lower()
        if prospect.ref in self._refs:
            return SuppressionResult(True, "ref on suppression list")
        if email in self._emails:
            return SuppressionResult(True, "email on suppression list")
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if domain and domain in self._domains:
            return SuppressionResult(True, f"domain @{domain} suppressed")
        return SuppressionResult(False, None)

    def is_suppressed(self, email: str) -> bool:
        return self.check(Prospect(email=email)).suppressed

    def __contains__(self, email: str) -> bool:  # ergonomic: "x@y" in gate
        return self.is_suppressed(email)

    @property
    def size(self) -> int:
        return len(self._emails) + len(self._domains) + len(self._refs)


def ref_of(email: str) -> str:
    """Convenience: the suppression ref for an email (for list authors)."""
    return prospect_ref(email)
