"""Canonical object model (P1.5 blueprint #1) — the durable substrate the run-state
grounds in.

The blueprint + progress board describe a run; THESE are the first-class business
entities the run operates on. Each is a small, typed Pydantic model. Entities with a
REAL data source are backed by a thin ``from_*`` adapter that reads that source; the
ones with no source yet are honest "not connected" stubs that raise
:class:`~studio.adapters.NotConfiguredError` (mirroring ``studio/adapters/``) — they
NEVER fabricate.

Backed by real sources today:
  * :class:`Lead`            ← ``customers`` (via :mod:`studio.customer_research`)
  * :class:`Offer`           ← the ``kind='offers'`` doc (via :mod:`studio.offers`)
  * :class:`ConversationThread` ← ``lead_conversations`` (via the message-source adapter)
  * :class:`Campaign`        ← ``runs`` (via :mod:`harness.runstore`)
  * :class:`SendReceipt`     ← ``actions`` (via :mod:`actions.store`)
  * :class:`Consent`         ← the customer's opt-out / status flags on the lead facts

Honest "not connected" stubs (no source wired yet):
  * :class:`Artist`, :class:`Shop`, :class:`Asset` — raise ``NotConfiguredError``.

This module reads only; it stages/sends nothing. It is a substrate, not a side effect.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from studio.adapters import NotConfiguredError


# --------------------------------------------------------------------------- #
# Entities with a REAL backing source.
# --------------------------------------------------------------------------- #
class Lead(BaseModel):
    """A prospect/customer — the normalized identity the run targets. Backed by the
    ``customers`` table via :mod:`studio.customer_research`."""

    customer_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    interests: list[str] = Field(default_factory=list)
    lifecycle_stage: str | None = None
    artist: str | None = None
    win_back_candidate: bool | None = None

    @classmethod
    def from_facts(cls, facts: dict[str, Any]) -> "Lead":
        """Build from the grounded-facts dict ``customer_research.lookup_leads`` returns
        (real values only; absent fields stay ``None``/empty — never defaulted to a fake)."""
        traits = facts.get("persona_traits", {}) or {}
        return cls(
            customer_id=facts["customer_id"],
            name=facts.get("name"),
            email=facts.get("email"),
            phone=facts.get("phone"),
            city=facts.get("city"),
            interests=list(facts.get("interests", []) or []),
            lifecycle_stage=traits.get("lifecycle_stage"),
            artist=facts.get("artist"),
            win_back_candidate=traits.get("win_back_candidate"),
        )


class Offer(BaseModel):
    """A substantiated offer — the only thing a draft may reference. Backed by the
    ``kind='offers'`` doc via :mod:`studio.offers` (fabricated codes fail closed there)."""

    code: str
    description: str = ""
    discount: str | None = None
    valid_until: str | None = None
    applies_to: list[str] = Field(default_factory=list)
    kind: str = "discount"

    @classmethod
    def from_offer(cls, offer: Any) -> "Offer":
        return cls(
            code=offer.code, description=offer.description, discount=offer.discount,
            valid_until=offer.valid_until, applies_to=list(offer.applies_to or []),
            kind=offer.kind,
        )


class ConversationThread(BaseModel):
    """One lead's prior conversation — the analyst's primary evidence. Backed by
    ``lead_conversations`` via the message-source adapter."""

    customer_id: str
    turns: list[dict[str, Any]] = Field(default_factory=list)
    connected: bool = True

    @classmethod
    def for_customer(cls, customer_id: str, tenant_id: str, *, dsn: str | None = None) -> "ConversationThread":
        """Real read of a lead's thread; an honest empty (``connected=False``) thread when
        the source is a not-connected stub or has no conversation for this lead."""
        try:
            from studio.adapters.message_source import DbConversationSource

            thread = DbConversationSource(tenant_id, dsn=dsn).thread_for(customer_id)
            turns = (thread or {}).get("turns", []) if isinstance(thread, dict) else (thread or [])
            return cls(customer_id=customer_id, turns=list(turns or []), connected=bool(turns))
        except NotConfiguredError:
            return cls(customer_id=customer_id, turns=[], connected=False)
        except Exception:
            return cls(customer_id=customer_id, turns=[], connected=False)


class CRMRecord(BaseModel):
    """The lead's CRM state (lifecycle, payment, artist). A view over the same real facts
    the ``customers`` store holds — distinct from :class:`Lead` (identity) as the mutable
    relationship record."""

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
        return cls(
            action_id=getattr(row, "id", "") or (row.get("id") if isinstance(row, dict) else ""),
            run_id=getattr(row, "run_id", None) if not isinstance(row, dict) else row.get("run_id"),
            channel=getattr(row, "channel", None) if not isinstance(row, dict) else row.get("channel"),
            target=getattr(row, "target", None) if not isinstance(row, dict) else row.get("target"),
            status=(getattr(row, "status", "pending") if not isinstance(row, dict) else row.get("status")) or "pending",
        )


class Consent(BaseModel):
    """The lead's contactability. Backed by the customer's opt-out / status flags. A lead
    is NOT contactable once it is explicitly opted-out or its status is
    unsubscribed/blocked; absence of an opt-out among the operator's OWN existing leads is
    contactable. (The real safety is the HOLD gate — every send is approve-first
    regardless — so this flag surfaces KNOWN opt-outs, it does not itself authorize a send.)"""

    customer_id: str
    opted_out: bool = False
    contactable: bool = False

    @classmethod
    def from_facts(cls, facts: dict[str, Any]) -> "Consent":
        traits = facts.get("persona_traits", {}) or {}
        opted_out = bool(facts.get("opted_out") or traits.get("opted_out"))
        status = (facts.get("status") or traits.get("status") or "").strip().lower()
        contactable = (not opted_out) and status not in ("unsubscribed", "blocked", "opted_out")
        return cls(customer_id=facts["customer_id"], opted_out=opted_out, contactable=contactable)


# --------------------------------------------------------------------------- #
# Entities with NO source wired yet — honest "not connected" stubs.
# --------------------------------------------------------------------------- #
class Artist(BaseModel):
    """A studio artist. NOT connected yet — no artist directory source exists. The stub
    raises rather than fabricating an artist (mirrors ``studio/adapters/artist_source``)."""

    id: str
    name: str | None = None

    @classmethod
    def load(cls, artist_id: str) -> "Artist":
        raise NotConfiguredError(
            "Artist directory is not connected yet — artist attribution comes only from a "
            "lead's own real facts. Wire an ArtistSource before loading an Artist entity."
        )


class Shop(BaseModel):
    """A studio shop/location. NOT connected yet — raises rather than inventing a shop."""

    id: str
    name: str | None = None

    @classmethod
    def load(cls, shop_id: str) -> "Shop":
        raise NotConfiguredError(
            "Shop/location directory is not connected yet — no shop source exists. "
            "Wire a ShopSource before loading a Shop entity."
        )


class Asset(BaseModel):
    """A creative asset (artwork/media) attached to a draft. NOT connected yet — artwork
    matching is a P4-gated capability; the stub raises rather than fabricating an asset."""

    id: str
    kind: str | None = None

    @classmethod
    def load(cls, asset_id: str) -> "Asset":
        raise NotConfiguredError(
            "Asset/artwork store is not connected yet (artwork matching is P4-gated). "
            "Wire an AssetSource before loading an Asset entity."
        )
