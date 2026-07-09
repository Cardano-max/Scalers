"""ResearchRouter — selects vetted providers, fans out a query, merges results.

The router is the research engine's entry point (bead 1mk.4). Given a
:class:`ResearchQuery` and the providers enabled for a tenant (from the pack's
``[research].sources``), it:

  1. picks the providers that serve the query's channels (intent-defaulted),
  2. calls each provider's ``gather`` (providers do the official-API I/O),
  3. merges + de-dupes signals/communities/creatives, sorts by confidence,
  4. enforces ``query.limit`` and records which sources answered,
  5. handles the bead's edge cases: **thin niche data** (return empty cleanly,
     never crash) and **competitor false positives** (low-confidence creatives
     are kept but flagged with a review note, never silently dropped/promoted).

The router itself does **no network** — it only orchestrates vetted providers.
A provider name that is not registered (not vetted) is skipped with a note, so
an un-vetted source can never be reached even if a pack lists it.
"""

from __future__ import annotations

from collections.abc import Iterable

from research.adapter import (
    Channel,
    Creative,
    Intent,
    ProviderResult,
    ResearchQuery,
    ResearchResult,
    Signal,
    SourceProvider,
)

# Which channels each intent defaults to when the query doesn't pin them — the
# tattoo-native retarget baked in (upstream HN/SaaS -> these).
_DEFAULT_CHANNELS: dict[Intent, tuple[Channel, ...]] = {
    "map_market": (Channel.R_TATTOOS, Channel.INSTAGRAM_HASHTAG, Channel.TIKTOK, Channel.PINTEREST),
    "find_communities": (Channel.R_TATTOOS, Channel.INSTAGRAM_HASHTAG, Channel.TIKTOK),
    "competitor_creatives": (Channel.META_AD_LIBRARY, Channel.INSTAGRAM_HASHTAG),
}

# Below this confidence a competitor creative is a likely false positive — kept,
# but flagged for operator review rather than dropped or promoted (bead edge case).
_FALSE_POSITIVE_FLOOR = 0.5


class ResearchRouter:
    """Fan a research query across the tenant's vetted, channel-matching providers."""

    def __init__(self, providers: Iterable[SourceProvider]) -> None:
        # name -> provider; a registry of what is vetted + wired.
        self._providers: dict[str, SourceProvider] = {p.name: p for p in providers}

    @classmethod
    def for_sources(
        cls, sources: Iterable[str], registry: dict[str, SourceProvider]
    ) -> "ResearchRouter":
        """Build a router for a pack's ``[research].sources`` list, using only
        providers present in ``registry`` (the vetted set). Unknown source names
        are dropped here — a pack cannot conjure an un-vetted provider."""
        chosen = [registry[s] for s in sources if s in registry]
        return cls(chosen)

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def _channels_for(self, query: ResearchQuery) -> tuple[Channel, ...]:
        return query.channels or _DEFAULT_CHANNELS.get(query.intent, ())

    def gather(self, query: ResearchQuery) -> ResearchResult:
        wanted = set(self._channels_for(query))
        signals: list[Signal] = []
        communities: list = []
        creatives: list[Creative] = []
        notes: list[str] = []
        used: list[str] = []
        raw_sources: list[dict] = []

        for name, provider in self._providers.items():
            # Only call a provider that serves at least one wanted channel.
            if wanted and not (provider.channels & wanted):
                continue
            try:
                result = provider.gather(query)
            except NotImplementedError:
                # Live provider not wired yet (eng); skip cleanly, note it.
                notes.append(f"provider '{name}' not yet wired (live client pending)")
                continue
            except Exception as exc:  # a flaky source must not sink the whole query
                # Honest degrade: name the provider AND the real reason (e.g. no key,
                # disabled, HTTP error) so it's clear why a citation is absent.
                notes.append(f"provider '{name}' failed: {type(exc).__name__}: {exc}")
                continue
            if isinstance(result, ProviderResult):
                signals.extend(result.signals)
                communities.extend(result.communities)
                creatives.extend(result.creatives)
                notes.extend(result.notes)
                raw_sources.extend(result.sources)
                used.append(name)

        signals = self._dedupe_signals(signals)
        communities = self._dedupe_communities(communities)
        creatives, fp_notes = self._flag_false_positives(self._dedupe_creatives(creatives))
        notes.extend(fp_notes)
        sources_cited = self._dedupe_sources(raw_sources)

        if not (signals or communities or creatives):
            # Thin niche data is normal, not an error — say so, return empty.
            notes.append("thin data: no tattoo-native signals for this query")

        return ResearchResult(
            query=query,
            signals=tuple(signals[: query.limit]),
            communities=tuple(communities[: query.limit]),
            creatives=tuple(creatives[: query.limit]),
            sources_used=tuple(used),
            notes=tuple(notes),
            sources_cited=tuple(sources_cited),
        )

    # ── merge helpers (dedupe preserves the highest-confidence variant) ──────

    @staticmethod
    def _dedupe_signals(items: list[Signal]) -> list[Signal]:
        best: dict[tuple[str, Channel], Signal] = {}
        for s in items:
            key = (s.text.strip().lower(), s.channel)
            if key not in best or s.confidence > best[key].confidence:
                best[key] = s
        return sorted(best.values(), key=lambda s: s.confidence, reverse=True)

    @staticmethod
    def _dedupe_communities(items: list) -> list:
        seen: dict[tuple[str, Channel], object] = {}
        for c in items:
            key = (c.name.strip().lower(), c.channel)
            seen.setdefault(key, c)
        return list(seen.values())

    @staticmethod
    def _dedupe_creatives(items: list[Creative]) -> list[Creative]:
        best: dict[tuple[str, Channel, str], Creative] = {}
        for c in items:
            key = (c.competitor.strip().lower(), c.channel, c.angle.strip().lower())
            if key not in best or c.confidence > best[key].confidence:
                best[key] = c
        return sorted(best.values(), key=lambda c: c.confidence, reverse=True)

    @staticmethod
    def _dedupe_sources(items: list[dict]) -> list[dict]:
        """Dedupe raw citable hits by url (first occurrence wins), dropping any with
        no real url. Order is preserved so the top-ranked hits persist first."""
        seen: set[str] = set()
        out: list[dict] = []
        for s in items:
            url = (s.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(s)
        return out

    @staticmethod
    def _flag_false_positives(items: list[Creative]) -> tuple[list[Creative], list[str]]:
        notes: list[str] = []
        for c in items:
            if c.confidence < _FALSE_POSITIVE_FLOOR:
                notes.append(
                    f"low-confidence competitor match flagged for review: "
                    f"{c.competitor} ({c.confidence:.2f}) — possible false positive"
                )
        return items, notes
