"""The read-only tool definitions — thin MCP wrappers over the existing sources.

Each tool is a :class:`ToolDef` (name / title / description / JSON-Schema
``inputSchema`` / ``readOnlyHint`` annotation / handler) that CONSUMES an existing
source adapter or tenant-scoped store **read-only** and returns a normalized,
structured result. Nothing here re-implements source logic, mutates a source, or
sends anything — the four families the ADR calls the standard supervisor-facing
contract (CRM / conversation / asset / offers) plus the offer-substantiation
gate:

  ┌ tool ───────────────────┬ wraps (read-only) ───────────────────────────────┐
  │ crm.list_leads          │ studio.adapters.lead_source (Csv / Stribe /       │
  │                         │   Mini-App)                                        │
  │ conversation.get_thread │ studio.adapters.message_source (DbConversation /  │
  │                         │   Uploaded / Stribe SMS / Mini-App notes)          │
  │ artist.list_artists     │ studio.adapters.artist_source (Seeded / Csv /     │
  │                         │   FutureMiniApp)                                   │
  │ offers.list_offers      │ studio.offers.get_offers (tenant doc store)        │
  │ offers.substantiate     │ studio.offers.substantiate (the no-fabrication     │
  │                         │   gate — an unknown code fails closed)             │
  │ assets.list_documents   │ studio.documents.list_documents (tenant assets     │
  │                         │   library)                                         │
  │ assets.retrieve         │ studio.documents.retrieve (tenant full-text search)│
  └─────────────────────────┴────────────────────────────────────────────────────┘

TENANT SCOPING is structural: every DB-backed handler reads with
``principal.tenant_id`` (passed by the server, never a caller-supplied value), so
a handler can only ever see its own tenant's rows. NOT-CONNECTED honesty is
structural too: a handler routed to a Stribe / Mini-App source lets the adapter's
:class:`~studio.adapters.NotConfiguredError` propagate — the server turns it into
an honest ``not_connected`` error and NEVER fabricates data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from studio.adapters.artist_source import (
    Artist,
    CsvArtistSource,
    FutureMiniAppArtistApi,
    SeededArtistSource,
)
from studio.adapters.lead_source import (
    CsvLeadSource,
    MiniAppCrmSource,
    StribeConversationSource,
)
from studio.adapters.message_source import (
    DbConversationSource,
    MiniAppCrmNotes,
    StribeSmsThread,
    UploadedConversationFile,
)

# A small REAL artist roster derived from the seeded demo leads (the three
# artists the seeded warm leads reference — see studio.seed_tattoo_leads
# SEED_ARTISTS). Seeded, DB-free, so ``artist.list_artists`` has real rows to
# return without a client API. Styles are the ones those leads asked each artist
# about; never fabricated beyond that.
_DEMO_ARTIST_ROSTER: list[Artist] = [
    Artist(name="Maya", styles=["fine-line", "floral"]),
    Artist(name="Rae", styles=["fine-line", "script"]),
    Artist(name="Noor", styles=["blackwork", "sleeve"]),
]


@dataclass
class ToolContext:
    """Server-provided, non-caller data a handler may need (e.g. the DB DSN)."""

    dsn: str | None = None


@dataclass(frozen=True)
class ToolDef:
    """One tool: its MCP metadata plus the handler that runs it.

    ``handler(principal, args, ctx)`` returns a plain JSON-able structure; the
    server validates ``args`` before calling and sanitizes the return after."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Any, dict[str, Any], ToolContext], Any]
    annotations: dict[str, Any] = field(
        default_factory=lambda: {"readOnlyHint": True, "destructiveHint": False}
    )

    def describe(self) -> dict[str, Any]:
        """The ``tools/list`` entry for this tool (spec 2025-11-25 shape)."""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": self.annotations,
        }


# --------------------------------------------------------------------------- #
# Normalizers — domain model → plain dict (real values only, never fabricated).
# --------------------------------------------------------------------------- #
def _lead_to_dict(lead: Any) -> dict[str, Any]:
    return asdict(lead)


def _artist_to_dict(artist: Any) -> dict[str, Any]:
    return asdict(artist)


def _offer_to_dict(offer: Any) -> dict[str, Any]:
    d = asdict(offer)
    d["evidence"] = offer.as_evidence()
    return d


def _thread_to_dict(thread: Any) -> dict[str, Any]:
    return {
        "customer_id": thread.customer_id,
        "turns": thread.turns,
        "channel": thread.channel,
        "source": thread.source,
        "campaign_message": thread.campaign_message,
        "has_turns": thread.has_turns,
    }


# --------------------------------------------------------------------------- #
# Source resolvers — map a validated ``source`` enum to the right adapter.
# The Stribe / Mini-App branches are the honest-stub seams: iterating them raises
# NotConfiguredError, which the server surfaces as a not_connected MCP error.
# --------------------------------------------------------------------------- #
def _lead_source(source: str, content: str):
    if source == "csv":
        return CsvLeadSource(content)
    if source == "stribe":
        return StribeConversationSource()
    if source == "miniapp":
        return MiniAppCrmSource()
    # Unreachable: the enum is validated upstream. Defensive, never fabricates.
    raise ValueError(f"unknown lead source {source!r}")


def _artist_source(source: str, content: str):
    if source == "seeded":
        return SeededArtistSource(list(_DEMO_ARTIST_ROSTER))
    if source == "csv":
        return CsvArtistSource(content)
    if source == "miniapp":
        return FutureMiniAppArtistApi()
    raise ValueError(f"unknown artist source {source!r}")


# --------------------------------------------------------------------------- #
# Handlers.
# --------------------------------------------------------------------------- #
def _handle_list_leads(principal, args, ctx: ToolContext) -> dict[str, Any]:
    source = args.get("source", "csv")
    limit = int(args.get("limit", 100))
    src = _lead_source(source, args.get("content", "") or "")
    leads: list[dict[str, Any]] = []
    for i, lead in enumerate(src.leads()):  # Stribe/Mini-App raise here (honest)
        if i >= limit:
            break
        leads.append(_lead_to_dict(lead))
    return {
        "tenant_id": principal.tenant_id,
        "source": getattr(src, "name", source),
        "count": len(leads),
        "leads": leads,
    }


def _handle_get_thread(principal, args, ctx: ToolContext) -> dict[str, Any]:
    source = args.get("source", "db")
    customer_id = args["customer_id"]
    if source == "db":
        # Tenant-scoped at the store: DbConversationSource is constructed with the
        # PRINCIPAL's tenant, so a customer under another tenant returns None.
        thread = DbConversationSource(
            principal.tenant_id, dsn=ctx.dsn
        ).thread_for(customer_id)
    elif source == "upload":
        thread = UploadedConversationFile(
            args.get("content", "") or "", customer_id=customer_id
        ).thread_for(customer_id)
    elif source == "stribe":
        thread = StribeSmsThread().thread_for(customer_id)  # NotConfiguredError
    elif source == "miniapp":
        thread = MiniAppCrmNotes().thread_for(customer_id)  # NotConfiguredError
    else:  # pragma: no cover - enum-validated upstream
        raise ValueError(f"unknown message source {source!r}")
    return {
        "tenant_id": principal.tenant_id,
        "source": source,
        "customer_id": customer_id,
        "found": thread is not None,
        "thread": _thread_to_dict(thread) if thread is not None else None,
    }


def _handle_list_artists(principal, args, ctx: ToolContext) -> dict[str, Any]:
    source = args.get("source", "seeded")
    limit = int(args.get("limit", 200))
    src = _artist_source(source, args.get("content", "") or "")
    artists: list[dict[str, Any]] = []
    for i, artist in enumerate(src.artists()):  # Mini-App raises here (honest)
        if i >= limit:
            break
        artists.append(_artist_to_dict(artist))
    return {
        "tenant_id": principal.tenant_id,
        "source": getattr(src, "name", source),
        "count": len(artists),
        "artists": artists,
    }


def _handle_list_offers(principal, args, ctx: ToolContext) -> dict[str, Any]:
    from studio.offers import get_offers

    offers = get_offers(principal.tenant_id, dsn=ctx.dsn)
    return {
        "tenant_id": principal.tenant_id,
        "count": len(offers),
        "offers": [_offer_to_dict(o) for o in offers],
    }


def _handle_substantiate(principal, args, ctx: ToolContext) -> dict[str, Any]:
    from studio.offers import get_offers, substantiate

    code = args["code"]
    offers = get_offers(principal.tenant_id, dsn=ctx.dsn)
    offer = substantiate(offers, code)
    return {
        "tenant_id": principal.tenant_id,
        "code": code,
        "substantiated": offer is not None,
        "offer": _offer_to_dict(offer) if offer is not None else None,
    }


def _handle_list_documents(principal, args, ctx: ToolContext) -> dict[str, Any]:
    from studio.documents import list_documents

    active_only = bool(args.get("active_only", True))
    docs = list_documents(principal.tenant_id, active_only=active_only, dsn=ctx.dsn)
    return {
        "tenant_id": principal.tenant_id,
        "active_only": active_only,
        "count": len(docs),
        "documents": docs,
    }


def _handle_retrieve(principal, args, ctx: ToolContext) -> dict[str, Any]:
    from studio.documents import retrieve

    query = args["query"]
    k = int(args.get("k", 5))
    hits = retrieve(principal.tenant_id, query, k, dsn=ctx.dsn)
    return {
        "tenant_id": principal.tenant_id,
        "query": query,
        "count": len(hits),
        "passages": hits,
    }


# --------------------------------------------------------------------------- #
# Tool registry.
# --------------------------------------------------------------------------- #
_LIMIT_PROP = {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}
_CONTENT_PROP = {
    "type": "string",
    "maxLength": 500_000,
    "description": "Raw uploaded content (CSV / transcript) for the non-DB sources.",
}


def default_tools() -> list[ToolDef]:
    """The full read-only tool set exposed by the default server."""
    return [
        ToolDef(
            name="crm.list_leads",
            title="List CRM Leads",
            description=(
                "List normalized leads for the calling tenant from a lead source. "
                "source=csv parses uploaded CSV content now; source=stribe|miniapp "
                "are not connected yet and return an honest not_connected error."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["csv", "stribe", "miniapp"],
                        "default": "csv",
                    },
                    "content": _CONTENT_PROP,
                    "limit": _LIMIT_PROP,
                },
                "required": [],
            },
            handler=_handle_list_leads,
        ),
        ToolDef(
            name="conversation.get_thread",
            title="Get Conversation Thread",
            description=(
                "Return one lead's normalized conversation thread for the calling "
                "tenant. source=db reads the tenant-scoped conversation store; "
                "source=upload parses an uploaded transcript; source=stribe|miniapp "
                "are not connected yet (honest not_connected error). A lead with no "
                "stored thread returns found=false — never a fabricated conversation."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["db", "upload", "stribe", "miniapp"],
                        "default": "db",
                    },
                    "customer_id": {"type": "string", "maxLength": 200, "minLength": 1},
                    "content": _CONTENT_PROP,
                },
                "required": ["customer_id"],
            },
            handler=_handle_get_thread,
        ),
        ToolDef(
            name="artist.list_artists",
            title="List Artists",
            description=(
                "List normalized artist profiles for the calling tenant. "
                "source=seeded returns the seeded studio roster; source=csv parses "
                "uploaded artist CSV; source=miniapp is not connected yet (honest "
                "not_connected error)."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["seeded", "csv", "miniapp"],
                        "default": "seeded",
                    },
                    "content": _CONTENT_PROP,
                    "limit": _LIMIT_PROP,
                },
                "required": [],
            },
            handler=_handle_list_artists,
        ),
        ToolDef(
            name="offers.list_offers",
            title="List Substantiated Offers",
            description=(
                "List the calling tenant's substantiated offers from its offers "
                "document. Honest-empty when no offers doc exists — the caller must "
                "not invent a discount."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
            handler=_handle_list_offers,
        ),
        ToolDef(
            name="offers.substantiate",
            title="Substantiate An Offer Code",
            description=(
                "The no-fabrication gate: return the real offer for a code iff it "
                "exists in the calling tenant's offers, else substantiated=false. An "
                "invented/unknown code fails closed and must not reach a draft."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "code": {"type": "string", "maxLength": 200, "minLength": 1},
                },
                "required": ["code"],
            },
            handler=_handle_substantiate,
        ),
        ToolDef(
            name="assets.list_documents",
            title="List Tenant Documents",
            description=(
                "List the calling tenant's documents (the persistent assets/"
                "knowledge library) as a compact index. Honest-empty when the tenant "
                "has no documents."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "active_only": {"type": "boolean", "default": True},
                },
                "required": [],
            },
            handler=_handle_list_documents,
        ),
        ToolDef(
            name="assets.retrieve",
            title="Retrieve From Tenant Assets",
            description=(
                "Full-text retrieve the top-k passages for a query across the calling "
                "tenant's ACTIVE documents. Returns [] when nothing matches — never a "
                "forced or fabricated passage."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "maxLength": 2_000, "minLength": 1},
                    "k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "required": ["query"],
            },
            handler=_handle_retrieve,
        ),
    ]


__all__ = ["ToolDef", "ToolContext", "default_tools"]
