"""Canonical object model (P1.5 blueprint #1) — the durable substrate the run grounds in.

This module does NOT fork a parallel model set. The entities that ALREADY exist as canonical
dataclasses are RE-EXPORTED here (single source of truth, field names + never-fabricated
defaults preserved), and their existing adapter **Protocols** remain the ONLY loaders:

  * :class:`Lead`               ← ``studio.adapters.lead_source.Lead``  (loaders: ``LeadSourceProtocol``)
  * :class:`ConversationThread` ← ``studio.adapters.message_source.ConversationThread`` (``MessageSourceProtocol``)
  * :class:`Artist` / :class:`Artwork` ← ``studio.adapters.artist_source`` (``ArtistSourceProtocol``)
  * :class:`Offer`              ← ``studio.offers.Offer``  (loader: ``studio.offers.get_offers``)

Only the entities with NO existing canonical type are DEFINED here, each backed by a REAL
source (never fabricated):

  * :class:`Campaign`    ← the ``runs`` store
  * :class:`CRMRecord`   ← the ``customers`` facts (lifecycle/payment/artist)
  * :class:`SendReceipt` ← the ``actions`` store (HELD/pending in this slice)
  * :class:`Consent`     ← the customer opt-in columns, promoted to a first-class typed
    field with provenance; the SMS/email channel gate routes through it.
  * :class:`Asset`       ← the REAL ``team.store`` ``assets`` table (NOT a stub).
  * :class:`Shop`        ← honest "not connected" stub (no shop directory source yet).

This module reads only; it stages/sends nothing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from studio.adapters import NotConfiguredError

# --------------------------------------------------------------------------- #
# Re-exports of the EXISTING canonical dataclasses (do NOT redefine — one source
# of truth; the adapter Protocols remain the only loaders).
# --------------------------------------------------------------------------- #
from studio.adapters.artist_source import (  # noqa: F401
    Artist,
    ArtistSourceProtocol,
    Artwork,
)
from studio.adapters.lead_source import Lead, LeadSourceProtocol  # noqa: F401
from studio.adapters.message_source import (  # noqa: F401
    ConversationThread,
    MessageSourceProtocol,
)
from studio.offers import Offer  # noqa: F401

__all__ = [
    "Lead", "LeadSourceProtocol",
    "ConversationThread", "MessageSourceProtocol",
    "Artist", "Artwork", "ArtistSourceProtocol",
    "Offer",
    "Campaign", "CRMRecord", "SendReceipt", "Consent", "Asset", "Shop",
    "channel_consented",
]


# --------------------------------------------------------------------------- #
# New canonical entities (no pre-existing type) — each REAL-backed.
# --------------------------------------------------------------------------- #
class Campaign(BaseModel):
    """One campaign run. Backed by the ``runs`` store (Run.id == run_id)."""

    run_id: str
    tenant_id: str | None = None
    status: str | None = None

    @classmethod
    def from_run(cls, record: Any) -> "Campaign":
        status = getattr(getattr(record, "status", None), "value", None) or str(
            getattr(record, "status", "") or ""
        )
        return cls(
            run_id=getattr(record, "run_id", ""),
            tenant_id=getattr(record, "tenant_id", None),
            status=status or None,
        )


class CRMRecord(BaseModel):
    """The lead's CRM state (lifecycle/payment/artist) — a view over the same real
    ``customers`` facts a :class:`Lead` identifies, as the mutable relationship record."""

    customer_id: str
    lifecycle_stage: str | None = None
    payment_status: str | None = None
    artist: str | None = None
    past_tattoos: int = 0

    @classmethod
    def from_facts(cls, facts: dict[str, Any]) -> "CRMRecord":
        traits = facts.get("persona_traits", {}) or {}
        return cls(
            customer_id=facts["customer_id"],
            lifecycle_stage=traits.get("lifecycle_stage"),
            payment_status=facts.get("payment_status") or traits.get("payment_status"),
            artist=facts.get("artist"),
            past_tattoos=len(facts.get("tattoo_history", []) or []),
        )


class SendReceipt(BaseModel):
    """A staged/held or terminal outreach action. Backed by the ``actions`` store. In this
    slice everything is HELD (``status='pending'``); nothing is 'sent'."""

    action_id: str
    run_id: str | None = None
    channel: str | None = None
    target: str | None = None
    status: str = "pending"

    @classmethod
    def from_action(cls, row: Any) -> "SendReceipt":
        get = (lambda k: row.get(k)) if isinstance(row, dict) else (lambda k: getattr(row, k, None))
        return cls(
            action_id=get("id") or "", run_id=get("run_id"), channel=get("channel"),
            target=get("target"), status=get("status") or "pending",
        )


class Consent(BaseModel):
    """The lead's contactability — a FIRST-CLASS typed field with provenance, derived from
    the customer opt-in columns. Organic channels (instagram/facebook) need no opt-in; email
    and SMS require an explicit opt-in. A global opt-out withholds every channel. The real
    safety is still the HOLD gate (every send is approve-first); this makes the consent basis
    auditable and routes the channel gate through ONE typed representation."""

    customer_id: str
    email: bool = False
    sms: bool = False
    opted_out: bool = False
    # Provenance: WHY/where the consent basis came from.
    basis: str = "opt_in_flag"
    source: str = "crm"
    granted_at: str | None = None

    def allows(self, channel: str) -> bool:
        """True iff the lead may be contacted on ``channel`` (never overriding a withheld
        opt-in). Behavior-identical to the prior inline gate: email/sms require the opt-in;
        instagram/facebook are organic (no opt-in), gated only by a global opt-out."""
        ch = (channel or "").strip().lower()
        if self.opted_out:
            return False
        if ch in ("email", "gmail"):
            return self.email
        if ch == "sms":
            return self.sms
        if ch in ("instagram", "ig", "facebook", "fb"):
            return True
        return False

    @classmethod
    def from_facts(cls, facts: dict[str, Any]) -> "Consent":
        traits = facts.get("persona_traits", {}) or {}
        return cls(
            customer_id=facts.get("customer_id", ""),
            email=bool(facts.get("email_opt_in")),
            sms=bool(facts.get("sms_opt_in")),
            opted_out=bool(facts.get("opted_out") or traits.get("opted_out")),
            source=(facts.get("source") or "crm"),
            granted_at=facts.get("consent_granted_at"),
        )


def channel_consented(facts: dict[str, Any], channel: str) -> bool:
    """The ONE consent gate the channel selection routes through: may this lead be contacted
    on ``channel``? A below-consent SMS returns False (the caller falls back to instagram —
    never overriding withheld consent)."""
    return Consent.from_facts(facts).allows(channel)


class Asset(BaseModel):
    """A produced creative asset. Backed by the REAL ``team.store`` ``assets`` table (NOT a
    stub) — the artifacts a run queues, HELD until approved (status starts 'queued')."""

    id: str
    campaign_id: str | None = None
    asset_type: str | None = None
    status: str = "queued"
    content: Any = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Asset":
        return cls(
            id=row.get("id", ""), campaign_id=row.get("campaign_id"),
            asset_type=row.get("asset_type"), status=row.get("status") or "queued",
            content=row.get("content"),
        )

    @classmethod
    def for_campaign(cls, campaign_id: str, *, dsn: str | None = None) -> list["Asset"]:
        """Real read of a campaign's queued assets from the team store (never fabricated;
        empty when the store is unavailable)."""
        try:
            from team.store import TeamStore

            ts = TeamStore(dsn)
            ts.setup()
            return [cls.from_row(r) for r in ts.list_assets(campaign_id)]
        except Exception:
            return []


class Shop(BaseModel):
    """A studio shop/location. NOT connected yet — no shop directory source exists. The
    loader raises rather than inventing a shop (mirrors ``studio/adapters``)."""

    id: str
    name: str | None = None

    @classmethod
    def load(cls, shop_id: str) -> "Shop":
        raise NotConfiguredError(
            "Shop/location directory is not connected yet — no shop source exists. "
            "Wire a ShopSource before loading a Shop entity."
        )
