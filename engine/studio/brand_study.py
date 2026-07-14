"""Cross-industry marketing intelligence (client direction, PA meeting 2026-07-11).

The client's ask: keep tattooing as the base, but "go far beyond that" — study how
the hottest brands in the US/EU (he named Skims) win attention, and mesh their
approach into our drafting. Three distinct objectives, per his framing: pure
FOLLOWER growth, heavier ENGAGEMENT, and SALES conversion — a good campaign mixes
all three.

Two honest layers, gated by the tenant's ``[brand_study]`` pack config:

  * a deterministic **principle library** — general, cross-industry hook
    archetypes per objective. These are marketing PRINCIPLES (how attention is
    won), NOT sourced claims about a specific brand, and are labelled as such so
    the drafter never states "Skims does X" without evidence.
  * an optional **live enrichment** — when ``enabled`` and an
    ``ANTHROPIC_API_KEY`` is armed, the Anthropic research provider web-searches
    for how named brands/industries hook their audience and returns REAL, citable
    sources. No key / disabled -> the principle library stands alone (honest —
    never a fabricated citation).

:func:`render_brand_study_block` renders the brief block that ORDERS the drafter
to blend these cross-industry hooks with the tattoo base — the molder keeps our
brand voice, artwork, and offers; it borrows only the SHAPE of what wins attention
across industries.
"""

from __future__ import annotations

import os
from typing import Any

# Deterministic, cross-industry hook ARCHETYPES per objective. Each is a reusable
# SHAPE (how attention is won), phrased as a principle — not a claim about any one
# brand. The drafter molds these onto our own artwork / voice / offers.
_PRINCIPLE_LIBRARY: dict[str, tuple[dict[str, str], ...]] = {
    "followers": (
        {"hook": "identity mirror — 'this is for people like you'",
         "why": "top DTC brands grow follows by making the feed feel like a "
                "membership, not an ad; the viewer follows to keep belonging"},
        {"hook": "serialized reveal — a format the audience returns for",
         "why": "recurring segments (weekly drop, process series) train the "
                "audience to come back, compounding follows over time"},
    ),
    "engagement": (
        {"hook": "polarizing take or two-option prompt",
         "why": "the highest-comment posts across industries ask a question the "
                "audience has an opinion on — a choice, not a broadcast"},
        {"hook": "before/after or transformation reveal",
         "why": "transformation content over-indexes on saves + shares because "
                "the payoff is visual and instantly re-shareable"},
    ),
    "sales": (
        {"hook": "scarcity tied to a real constraint",
         "why": "the best sales hooks name a TRUE limit (a few slots, a closing "
                "date) — credible scarcity converts where fake urgency erodes trust"},
        {"hook": "objection-first framing",
         "why": "leading with the reason people hesitate (price, time, fear) and "
                "resolving it out-converts feature-led copy"},
    ),
}

_VALID_OBJECTIVES = tuple(_PRINCIPLE_LIBRARY.keys())


def principles_for(objectives: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
    """The principle archetypes for the requested objectives, in order, deduped.
    Unknown objective names are dropped (never invented). Empty request -> all
    three objectives (the client's 'mix of all of those things')."""
    wanted = [str(o).strip().lower() for o in objectives if str(o or "").strip()]
    if not wanted:
        wanted = list(_VALID_OBJECTIVES)
    out: list[dict[str, str]] = []
    for obj in wanted:
        for arch in _PRINCIPLE_LIBRARY.get(obj, ()):
            out.append({"objective": obj, **arch})
    return out


def study_cross_industry(
    *,
    objectives: tuple[str, ...] | list[str] = (),
    industries: tuple[str, ...] | list[str] = (),
    seed_brands: tuple[str, ...] | list[str] = (),
    max_brands: int = 8,
    provider: Any = None,
    env: Any = None,
) -> dict[str, Any]:
    """Assemble the cross-industry study: the deterministic principle set, plus —
    when a live research ``provider`` is armed — REAL citable sources on how the
    named brands/industries hook attention.

    Returns ``{principles, sources, note}``. ``sources`` is always verbatim
    provider hits (``{query,url,title,snippet}``) or empty — never fabricated.
    ``provider`` defaults to the Anthropic research provider built from
    ``ANTHROPIC_API_KEY``; absent key -> principles only, honestly noted.
    """
    e = env if env is not None else os.environ
    principles = principles_for(objectives)

    if provider is None:
        # Go-live posture is TWO explicit gates, both required before any egress:
        #   (1) the tenant's [brand_study] enabled=true (default OFF) — the only caller,
        #       study_for_tenant, returns None otherwise, so reaching here already means
        #       the operator opted this tenant into live enrichment; and
        #   (2) an armed ANTHROPIC_API_KEY.
        # enabled=True is therefore correct at this point (the opt-in is upstream). No
        # key -> principle library only, honestly noted; nothing is ever fabricated.
        key = (e.get("ANTHROPIC_API_KEY") or "").strip()
        if key:
            from research.providers.anthropic_research import AnthropicResearchProvider

            provider = AnthropicResearchProvider(api_key=key, enabled=True)

    sources: list[dict[str, Any]] = []
    notes: list[str] = []
    if provider is None:
        notes.append(
            "cross-industry study: principle library only "
            "(no live research provider armed — nothing fabricated)"
        )
        return {"principles": principles, "sources": sources, "note": "; ".join(notes)}

    targets = [str(b).strip() for b in seed_brands if str(b or "").strip()]
    targets += [str(i).strip() for i in industries if str(i or "").strip()]
    seen: set[str] = set()
    for target in targets[: max(1, int(max_brands))]:
        query = f"how {target} wins attention on social media hooks strategy"
        try:
            hits = provider.search(query, limit=3)
        except Exception as exc:  # noqa: BLE001 — one failed target never fabricates
            notes.append(f"study failed for {target!r}: {type(exc).__name__}: {exc}")
            continue
        for h in hits:
            if h.url in seen:
                continue
            seen.add(h.url)
            sources.append(
                {"query": query, "url": h.url, "title": h.title, "snippet": h.snippet}
            )
    if not sources and not notes:
        notes.append("cross-industry study: web research returned no usable sources")
    return {"principles": principles, "sources": sources, "note": "; ".join(notes)}


def study_for_tenant(tenant_id: str, *, provider: Any = None, env: Any = None) -> dict[str, Any] | None:
    """Run the study for a tenant iff its ``[brand_study]`` pack config is enabled.

    Returns ``None`` (no block) when disabled / no pack — an un-configured tenant
    keeps the tattoo-only behavior. Best-effort: a broken pack never breaks a run.
    """
    try:
        from config.loader import load_pack

        cfg = getattr(load_pack(tenant_id), "brand_study", None)
    except Exception:  # no/corrupt pack → no study
        return None
    if cfg is None or not getattr(cfg, "enabled", False):
        return None
    return study_cross_industry(
        objectives=cfg.objectives,
        industries=cfg.industries,
        seed_brands=cfg.seed_brands,
        max_brands=cfg.max_brands,
        provider=provider,
        env=env,
    )


def render_brand_study_block(study: dict[str, Any] | None) -> str:
    """The brief block ordering the drafter to BLEND cross-industry hooks with the
    tattoo base — or ``""`` when there is no study (the tattoo-only default). The
    principles are shapes to mold, never claims to state; the sources (if any) are
    real, citable evidence the drafter may draw on."""
    if not study or not study.get("principles"):
        return ""
    lines = [
        "\nCROSS-INDUSTRY MARKETING INTELLIGENCE — the base stays tattoo, but blend "
        "in how the best brands across industries win attention. Take the SHAPE of "
        "these hooks; keep OUR artwork, brand voice, and substantiated offers. These "
        "are marketing PRINCIPLES, not claims about a specific brand — never state "
        "'brand X does Y' unless a cited source below supports it.",
    ]
    by_obj: dict[str, list[dict[str, str]]] = {}
    for p in study["principles"]:
        by_obj.setdefault(p.get("objective", "general"), []).append(p)
    labels = {"followers": "grow followers", "engagement": "drive engagement",
              "sales": "convert to bookings/sales"}
    for obj, items in by_obj.items():
        lines.append(f"  - objective — {labels.get(obj, obj)}:")
        for it in items:
            lines.append(f"      • {it['hook']} ({it['why']})")
    sources = study.get("sources") or []
    if sources:
        lines.append("  - cited cross-industry evidence (real sources — may quote):")
        for s in sources[:6]:
            title = s.get("title") or s.get("url")
            lines.append(f"      • {title} — {s.get('url')}")
    elif study.get("note"):
        lines.append(f"  - note: {study['note']}")
    return "\n".join(lines)
