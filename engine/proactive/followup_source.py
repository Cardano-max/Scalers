"""Data-backed inputs for the follow-up detector (CustomerAcq-fr1.1 detector #1).

Keeps the detector itself pure: this module resolves the exclusion sets from real
tables so ``follow_up_opportunities`` stays unit-testable. Opt-out is read from the
t90.3 suppression ledger (the canonical STOP/DND source).

Responder exclusion note: the trunk has NO inbound-reply/responder table yet (only
opt-out, sends, and provider delivery events). So ``resolve_responded`` is a
deliberate seam that returns empty today; when inbound-reply capture lands it reads
from there. Until then the follow-up set is opt-out-only — surfaced honestly, never
faked as if a positive-reply signal existed.
"""

from __future__ import annotations

from collections.abc import Sequence


def resolve_opted_out(
    *, tenant_id: str, identifiers: Sequence[str], channel: str, dsn: str | None = None
) -> frozenset[str]:
    """The caller's identifiers that are suppressed (opted out) on ``channel`` or
    cross-channel, per the t90.3 suppression ledger. These are excluded from any
    follow-up proposal — the scanner never chases someone who said STOP."""
    if not identifiers:
        return frozenset()
    from suppression.ledger import filter_audience

    result = filter_audience(
        tenant_id=tenant_id, identifiers=list(identifiers), channel=channel, dsn=dsn
    )
    return frozenset(identifier for identifier, _reason in result.removed)


def resolve_responded(
    *, tenant_id: str, identifiers: Sequence[str], dsn: str | None = None
) -> frozenset[str]:
    """Recipients who already replied — excluded from follow-ups. No trunk table
    records inbound replies yet, so this returns empty (opt-out-only exclusion) until
    the engagement inbound-capture lands. Kept as an explicit seam, not silently
    dropped, so the AC-8 contract is honoured the moment a reply signal exists."""
    return frozenset()
