"""Exactly-once side-effect boundary (systemdesign §3 + §6.4, HARN-04).

The ONLY way a side effect (IG post, Gmail send, DM) happens. Three mechanisms
combine — a deterministic key, a UNIQUE-constrained ledger/outbox, and a durable
``SENDING`` claim committed before the external call — enforced at the database,
independent of the orchestration substrate.

IMPORTANT: for a non-transactional external effect, exactly-once is only
achievable if the **connector is keyed/idempotent** (it dedupes on the
idempotency key). That is the two-generals problem and the real IG/Gmail APIs
provide the token; see :mod:`sideeffects.dispatcher` for the contract.
Without an idempotent connector a crash in the send→commit window can still
double-fire — the boundary minimizes that window and the redundant call, but
cannot abolish it.
"""

from sideeffects.capture import capture_engagement, capture_provider_result, redact_pii
from sideeffects.keys import Channel, idempotency_key
from sideeffects.provider import ProviderResult, as_provider_result

__all__ = [
    "Channel",
    "idempotency_key",
    "ProviderResult",
    "as_provider_result",
    "capture_engagement",
    "capture_provider_result",
    "redact_pii",
]
