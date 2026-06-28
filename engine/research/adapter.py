"""Research adapter — the vetted seam between the research skills and the network.

Bead 1mk.4. The three adopted research skills (map-your-market,
where-your-customer-lives, competitor-pr-finder) are **prompt-only methodology**:
they describe HOW to mine tattoo-native demand signals, communities, and
competitor creatives, but they do **no network themselves**. Every fetch is
delegated to a :class:`SourceProvider` behind this adapter.

Why a seam (supply-chain safety, per docs/skills/vetting-protocol.md): the
upstream skills shipped a ``fetch.py`` that disabled TLS verification
(``ssl._create_unverified_context`` / ``CERT_NONE``) and read ``GITHUB_TOKEN`` /
``.env``. That script is **stripped** (never vendored). All access instead goes
through official, TLS-verified provider APIs (Firecrawl for the web/social
surface; Foreplay / Meta Ad Library for competitor ad creatives) — official APIs
only, no scraping, no credential harvesting. The skills name *intents +
tattoo-native channels*; the providers (wired by eng) make the calls.

This module is the **contract**: the typed shapes + the ``SourceProvider``
protocol. The live providers live in ``research/providers/`` (eng-implemented);
``FixtureProvider`` is the deterministic offline stand-in used by tests and as
the safe default until live clients land.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol, runtime_checkable

# ── Channels (tattoo-native, the retarget from the upstream HN/SaaS sources) ──


class Channel(str, Enum):
    """Where tattoo demand/communities/competitors actually live.

    The retarget (bead 1mk.4): upstream mined Reddit/HN/GitHub/G2/DuckDuckGo;
    we mine tattoo-native surfaces. The *phrasing/method* is the asset, the
    channels are not — see references/tattoo-native-sources.md in each skill.
    """

    R_TATTOOS = "r_tattoos"               # r/tattoos, r/TattooDesigns, r/tattoo
    INSTAGRAM_HASHTAG = "instagram_hashtag"  # #fineline, #blackworktattoo, city tags
    PINTEREST = "pinterest"               # boards / pins (flash, style refs)
    TIKTOK = "tiktok"                     # tags + sounds (process/reveal videos)
    META_AD_LIBRARY = "meta_ad_library"   # competitor studio ads (Foreplay primary)
    WEB = "web"                           # artist sites / public pages (Firecrawl)


Intent = Literal["map_market", "find_communities", "competitor_creatives"]
"""Which skill is asking. map_market = map-your-market (pain/demand/angles);
find_communities = where-your-customer-lives; competitor_creatives =
competitor-pr-finder."""


# ── Typed payloads (frozen; mirrors kb/schema.py dataclass style) ────────────


@dataclass(frozen=True)
class ResearchQuery:
    """One research ask from a skill, scoped to a tenant + tattoo-native channels."""

    intent: Intent
    niche: str                                  # e.g. "fine-line tattoo, Brooklyn"
    seed_terms: tuple[str, ...] = ()            # hashtags / styles / competitor handles
    channels: tuple[Channel, ...] = ()          # empty = let the router pick by intent
    tenant_id: str | None = None
    competitor: str | None = None               # for competitor_creatives
    limit: int = 20


@dataclass(frozen=True)
class Signal:
    """A demand/pain/angle signal mined from a tattoo-native source."""

    text: str
    channel: Channel
    kind: Literal["pain", "demand", "angle"]
    confidence: float = 0.5
    url: str | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Community:
    """A place tattoo clients gather + how to enter it (per-channel entry tactic)."""

    name: str
    channel: Channel
    entry_tactic: str
    url: str | None = None
    size_hint: str | None = None


@dataclass(frozen=True)
class Creative:
    """A competitor's ad/post + the angle that made it work (the cold-pitch seed)."""

    competitor: str
    channel: Channel
    angle: str
    hook: str | None = None
    format: str | None = None
    url: str | None = None
    confidence: float = 0.5
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Document:
    """A page fetched through a provider — TLS verification is NON-NEGOTIABLE."""

    url: str
    text: str
    title: str | None = None
    fetched_via: str = "unknown"
    tls_verified: bool = True


@dataclass(frozen=True)
class ProviderResult:
    """What one provider returns for a query (the router merges across providers)."""

    signals: tuple[Signal, ...] = ()
    communities: tuple[Community, ...] = ()
    creatives: tuple[Creative, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchResult:
    """The merged, deduped research the engine hands back (the skill's output)."""

    query: ResearchQuery
    signals: tuple[Signal, ...] = ()
    communities: tuple[Community, ...] = ()
    creatives: tuple[Creative, ...] = ()
    sources_used: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not (self.signals or self.communities or self.creatives)


# ── Provider protocol (the seam eng implements with live, TLS-verified APIs) ──


@runtime_checkable
class SourceProvider(Protocol):
    """A vetted source backend. Implementations do official-API I/O ONLY —
    TLS verified, no scraping bans, no credential harvesting. The skills never
    call the network directly; they go through providers selected by the router.
    """

    name: str                       # matches a pack [research].sources entry
    channels: frozenset[Channel]    # which channels this provider can serve

    def gather(self, query: ResearchQuery) -> ProviderResult:
        """Return signals/communities/creatives for the query's intent + channels."""
        ...

    def fetch(self, url: str) -> Document:
        """Fetch one URL (TLS-verified). Optional; raise if unsupported."""
        ...


class UnsupportedIntent(RuntimeError):
    """A provider was asked for an intent/channel it does not serve."""
