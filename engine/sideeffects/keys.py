"""Deterministic idempotency-key derivation (systemdesign §3, HARN-04).

The key is the linchpin of the exactly-once guarantee: the same logical side
effect must always derive the same key so the database UNIQUE constraint can
recognise a duplicate across crashes, retries, and concurrent runs.
"""

from __future__ import annotations

import hashlib
from enum import Enum


class Channel(str, Enum):
    """The side-effect channels a tenant can act on."""

    POSTING = "posting"
    OUTREACH = "outreach"
    ENGAGEMENT = "engagement"


def idempotency_key(
    tenant: str, channel: Channel | str, target: str, content: str
) -> str:
    """Derive a stable key for one logical side effect.

    Shape: ``tenant:channel:target:contenthash`` (e.g. ``nw:outreach:bayside-pg:c8821``).

    Uses SHA-256 (not Python's salted ``hash()``) so the same inputs yield the
    same key in any process — without that, the UNIQUE constraint could not
    dedupe a retry that runs after a crash/restart.
    """
    channel_value = channel.value if isinstance(channel, Channel) else str(channel)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"{tenant}:{channel_value}:{target}:{content_hash}"
