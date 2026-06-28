"""Exactly-once side-effect boundary (systemdesign §3 + §6.4, HARN-04).

The ONLY way a side effect (IG post, Gmail send, DM) happens. Idempotent by
construction: a deterministic key + a UNIQUE constraint + a transactional
outbox make at-least-once dispatch effectively exactly-once, independent of the
orchestration substrate.
"""

from engine.sideeffects.keys import Channel, idempotency_key

__all__ = ["Channel", "idempotency_key"]
