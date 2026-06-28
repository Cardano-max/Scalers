"""Deliverability verifier (bead 1mk.7) — the adapted `cold-email-verifier`.

The upstream `cold-email-verifier` "guesses, enriches, and verifies emails from a
CSV autonomously." We **strip the guess + enrich + autonomous-CSV behavior** (that
is data-broker enrichment — apollo/hunter/zoominfo class, REJECTED in the
registry) and keep ONLY deterministic **verification** — the 1mk.3 move: adopt the
skill as a deterministic in-house validator, never an off-the-shelf classifier or
a broker call.

What this does (pure, hermetic, no network, no send):
- syntax (RFC-ish) → reject malformed,
- disposable / throwaway domains → undeliverable,
- role accounts (info@, sales@…) → risky (escalate, don't auto),
- shape heuristics (no MX *confirmation* without a live check).

What it does NOT do: it never enriches/guesses an address, never hits a data
broker, never sends. A live MX/SMTP probe can upgrade a `risky` verdict — that is
an eng seam (``mx_check``), TLS/own-resolver, not done here.
"""

from __future__ import annotations

import re

from outreach.schema import VerificationVerdict

# Deliberately conservative RFC-5322-ish local + domain check (not exhaustive).
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Throwaway/disposable domains — a non-exhaustive seed; the live check extends it.
_DISPOSABLE = frozenset(
    {
        "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
        "trashmail.com", "yopmail.com", "sharklasers.com", "getnada.com",
        "throwawaymail.com", "maildrop.cc", "dispostable.com",
    }
)

# Role accounts: deliverable but low-quality / complaint-prone -> risky, escalate.
_ROLE_LOCALPARTS = frozenset(
    {
        "info", "admin", "sales", "support", "contact", "hello", "team",
        "office", "billing", "marketing", "noreply", "no-reply", "postmaster",
        "abuse", "webmaster",
    }
)


def verify_email(email: str) -> VerificationVerdict:
    """Deterministic deliverability verdict for one address. No network, no send."""
    raw = (email or "").strip()
    if not raw or not _EMAIL_RE.match(raw):
        return VerificationVerdict("undeliverable", ("malformed address",))

    local, domain = raw.lower().rsplit("@", 1)

    if domain in _DISPOSABLE:
        return VerificationVerdict("undeliverable", (f"disposable domain @{domain}",))

    reasons: list[str] = []
    status = "deliverable"

    if local in _ROLE_LOCALPARTS:
        status = "risky"
        reasons.append(f"role account ({local}@) — complaint-prone, review")

    # Heuristic shape flags that warrant a human look but aren't fatal.
    if local.count(".") >= 4 or len(local) > 40:
        status = "risky"
        reasons.append("unusual local-part shape")

    # No live MX confirmation here: a freshly-syntactic address we cannot confirm
    # is 'deliverable' by shape but the live mx_check seam may downgrade it.
    return VerificationVerdict(status, tuple(reasons))


class DeliverabilityVerifier:
    """Verifier facade. The deterministic path is always on; an injected
    ``mx_check`` (eng seam) may refine a verdict with a live, TLS-verified probe."""

    def __init__(self, mx_check=None) -> None:
        # mx_check: Callable[[str], bool] | None — live MX/SMTP probe (eng-owned).
        self._mx_check = mx_check

    def verify(self, email: str) -> VerificationVerdict:
        verdict = verify_email(email)
        if verdict.status == "undeliverable" or self._mx_check is None:
            return verdict
        domain = email.strip().lower().rsplit("@", 1)[-1]
        try:
            has_mx = self._mx_check(domain)
        except Exception:
            return verdict  # live check failed; keep the deterministic verdict
        if not has_mx:
            return VerificationVerdict("undeliverable", verdict.reasons + ("no MX record",))
        return verdict
