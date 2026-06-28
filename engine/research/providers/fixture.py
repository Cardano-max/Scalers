"""FixtureProvider — deterministic, offline, tattoo-native research stand-in.

No network. Produces reproducible tattoo-flavored signals/communities/creatives
derived from the query, so:
  * the research engine + router are exercised end-to-end without live APIs,
  * tests are hermetic (no keys, no I/O),
  * it is the safe default until eng wires the live Firecrawl / Meta-Ad-Library
    providers behind the same :class:`SourceProvider` protocol.

It is intentionally simple (keyword-shaped, not semantic) — it stands in for the
real providers the way ``DeterministicEmbedder`` stands in for a real model in
the eval KB. It must never be used as a real research source in production.
"""

from __future__ import annotations

from research.adapter import (
    Channel,
    Community,
    Creative,
    ProviderResult,
    ResearchQuery,
    Signal,
)

_ALL_CHANNELS = frozenset(Channel)


class FixtureProvider:
    """Deterministic tattoo-native fixtures for every channel/intent."""

    name = "fixture"
    channels: frozenset[Channel] = _ALL_CHANNELS

    def gather(self, query: ResearchQuery) -> ProviderResult:
        niche = query.niche.strip() or "tattoo"
        terms = [t for t in query.seed_terms if t.strip()]
        # A query with no usable niche signal models the thin-data path.
        if niche.lower() in {"", "unknown", "n/a"} and not terms:
            return ProviderResult(notes=("fixture: no seed terms; thin data",))

        if query.intent == "map_market":
            return ProviderResult(signals=self._signals(niche, terms))
        if query.intent == "find_communities":
            return ProviderResult(communities=self._communities(niche, terms))
        if query.intent == "competitor_creatives":
            return ProviderResult(creatives=self._creatives(query))
        return ProviderResult()

    # ── map-your-market: pain/demand/angle signals ───────────────────────────

    def _signals(self, niche: str, terms: list[str]) -> tuple[Signal, ...]:
        tag = (terms[0] if terms else niche).lstrip("#")
        return (
            Signal(
                text=f"people asking how much a {niche} piece should cost / "
                f"whether the quote is fair",
                channel=Channel.R_TATTOOS, kind="pain", confidence=0.82,
                url="https://www.reddit.com/r/tattoos/",
                evidence=("recurring 'is this price fair' threads",),
            ),
            Signal(
                text=f"strong saved/again demand for {tag} flash and cover-ups",
                channel=Channel.PINTEREST, kind="demand", confidence=0.74,
                evidence=(f"#{tag} board saves",),
            ),
            Signal(
                text=f"process / healed-result reveals out-perform static flatlays for {niche}",
                channel=Channel.TIKTOK, kind="angle", confidence=0.7,
                evidence=("reveal-format videos trend in-niche",),
            ),
            Signal(
                text=f"first-tattoo nervousness — 'will it hurt / how do I pick an artist' for {niche}",
                channel=Channel.INSTAGRAM_HASHTAG, kind="pain", confidence=0.66,
                evidence=(f"#{tag} comment questions",),
            ),
        )

    # ── where-your-customer-lives: communities + entry tactics ───────────────

    def _communities(self, niche: str, terms: list[str]) -> tuple[Community, ...]:
        tag = (terms[0] if terms else niche).lstrip("#")
        return (
            Community(
                name="r/tattoos", channel=Channel.R_TATTOOS,
                entry_tactic="answer 'is this design/price fair' threads with genuine "
                "guidance, never a pitch; link only when asked",
                url="https://www.reddit.com/r/tattoos/", size_hint="large",
            ),
            Community(
                name=f"#{tag} (Instagram)", channel=Channel.INSTAGRAM_HASHTAG,
                entry_tactic="reply on local + style hashtags with technique value; "
                "geo-tag posts so city clients surface it",
                size_hint="medium",
            ),
            Community(
                name=f"{niche} Pinterest boards", channel=Channel.PINTEREST,
                entry_tactic="publish flash as pins with rich descriptions so saves "
                "feed discovery; board by style",
                size_hint="medium",
            ),
            Community(
                name="TikTok tattoo tags + sounds", channel=Channel.TIKTOK,
                entry_tactic="post process/reveal clips on trending sounds; pin a "
                "'how to book' comment",
                size_hint="large",
            ),
        )

    # ── competitor-pr-finder: competitor creatives + the winning angle ───────

    def _creatives(self, query: ResearchQuery) -> tuple[Creative, ...]:
        competitor = query.competitor or (query.seed_terms[0] if query.seed_terms else None)
        if not competitor:
            return ()
        return (
            Creative(
                competitor=competitor, channel=Channel.META_AD_LIBRARY,
                angle="scarcity: 'guest-spot dates open' drives bookings",
                hook="3 guest-spot days left this month",
                format="carousel of healed work", confidence=0.78,
                url="https://www.facebook.com/ads/library/",
                evidence=("long-running ad in Meta Ad Library",),
            ),
            Creative(
                competitor=competitor, channel=Channel.INSTAGRAM_HASHTAG,
                angle="proof: side-by-side cover-up transformations",
                hook="from regret to obsessed —",
                format="before/after reel", confidence=0.62,
                evidence=("top-engaged reel format",),
            ),
            # A deliberately weak match -> exercises the false-positive flag path.
            Creative(
                competitor=competitor, channel=Channel.META_AD_LIBRARY,
                angle="unclear / possibly a different studio with a similar handle",
                confidence=0.34,
                evidence=("name collision; low match",),
            ),
        )

    def fetch(self, url: str):  # pragma: no cover - fixtures don't fetch
        from research.adapter import Document

        return Document(url=url, text="(fixture: no live fetch)", fetched_via="fixture")
