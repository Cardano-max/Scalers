"""Sandbox execution connector for the tlv.6 dummy-tenant demo slice.

The demo needs the approve -> EXECUTE loop to close and show a real "delivered"
event in Runs/Activity WITHOUT any live provider credential. This connector is that
safe sink: it never opens a socket, never touches a provider, and is honestly
``is_mock = True`` — so the defense-in-depth :func:`actions.publish._ensure_real`
guard would refuse it on any REAL channel. The demo channel routes to
``_publish_demo`` (which deliberately skips ``_ensure_real``), so a sandbox delivery
happens ONLY through the explicitly-labeled demo path, never a real IG/Gmail/FB send.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DeliveryReceipt:
    """The synthetic delivery record a sandbox send produces (mirrors the shape the
    real connectors return: a deep_link the Live-Feed can show)."""

    deep_link: str
    delivered_at: datetime


class DemoConnector:
    """Records a delivery locally and returns a receipt. No network, no credential."""

    #: Marks this as a mock so ``_ensure_real`` refuses it on any real channel.
    is_mock = True

    def deliver(self, *, to: str, subject: str, body: str) -> DeliveryReceipt:
        """'Deliver' the message to the sandbox and return a receipt. Deterministic
        synthetic deep_link so the same content yields a stable, inspectable id."""
        token = hashlib.sha256(f"{to}|{subject}|{body}".encode()).hexdigest()[:12]
        return DeliveryReceipt(
            deep_link=f"sandbox://demo/{token}",
            delivered_at=datetime.now(timezone.utc),
        )
