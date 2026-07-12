"""Per-customer location resolution (client direction, PA meeting 2026-07-11).

The client asked to target by the CUSTOMER's location, not just the studio's — a
campaign for a lead in one city shouldn't be scoped to the studio's city. This
resolves a lead's location honestly, on-file first:

  1. the customer's own ``city`` / ``state`` fields (from the CRM or the Ink Pulse
     import) — deterministic, high-confidence, no network;
  2. a location the deep-research agent already extracted into the persona facts;
  3. otherwise UNRESOLVED — :func:`location_search_query` builds the search string
     for the existing research agent to find it. It is NEVER guessed and NEVER
     defaulted to the studio's city (that would target the wrong place).

HONESTY: a resolved location always names its ``source`` so downstream copy can be
gated — the personalization guard only lets a draft mention a fact that is
grounded, so an unresolved location must not appear in copy. The web path is
non-deterministic by nature (the client's own framing); this module only prepares
the query, it does not fabricate a location.
"""

from __future__ import annotations

import re
from typing import Any

# Two-letter US state codes → so "Austin, TX" and a bare "TX" both resolve a state.
_US_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
})


def _clean(v: Any) -> str:
    return str(v or "").strip()


def resolve_customer_location(facts: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve a lead's location from grounded facts (deterministic, no network).

    Reads, in order: explicit ``city``/``state`` → a ``location`` string
    ("Austin, TX") → a persona-extracted ``city`` — and returns::

        {"city", "state", "display", "source", "confident"}

    ``source`` is ``"on_file"`` (CRM/Ink Pulse fields), ``"persona"`` (research
    already extracted it), or ``"none"``. ``confident`` is True only when a real
    city is on file. Honest-empty (``source="none"``, ``confident=False``) when
    nothing is known — the caller then targets nothing on location, or triggers a
    location search; it NEVER falls back to the studio city."""
    f = facts or {}
    city = _clean(f.get("city"))
    state = _clean(f.get("state"))
    source = "on_file" if city else ""

    # A combined "City, ST" location string (from the Ink Pulse import or CRM).
    if not city:
        loc = _clean(f.get("location"))
        if loc:
            city, state = _parse_location_string(loc)
            source = "on_file" if city else source

    # A location a research pass already extracted into persona traits.
    if not city:
        persona = f.get("persona") if isinstance(f.get("persona"), dict) else {}
        p_city = _clean(persona.get("city") or persona.get("location"))
        if p_city:
            city, p_state = _parse_location_string(p_city)
            state = state or p_state
            source = "persona"

    state = state.upper() if state.upper() in _US_STATES else state
    display = ", ".join(x for x in (city, state) if x)
    return {
        "city": city, "state": state, "display": display,
        "source": source or "none", "confident": bool(city),
    }


def _parse_location_string(loc: str) -> tuple[str, str]:
    """('Austin', 'TX') from 'Austin, TX'; ('Austin', '') from 'Austin'. A bare
    2-letter state with no city yields ('', 'TX'). Pure, tolerant."""
    parts = [p.strip() for p in re.split(r"[,/|]", loc) if p.strip()]
    if not parts:
        return "", ""
    if len(parts) == 1:
        one = parts[0]
        if one.upper() in _US_STATES:
            return "", one.upper()
        return one, ""
    city = parts[0]
    state = parts[1]
    return city, (state.upper() if state.upper() in _US_STATES else state)


def location_search_query(facts: dict[str, Any] | None) -> str | None:
    """A search string for the research agent to find a lead's location, or
    ``None`` when it is already resolved (no search needed) or there is nothing to
    search on (no name/handle — never a fabricated query).

    Built ONLY from real handles the customer provided (name + instagram) — the
    honest, consented signal, not name-discovery of a stranger."""
    resolved = resolve_customer_location(facts)
    if resolved["confident"]:
        return None
    f = facts or {}
    name = _clean(f.get("name"))
    ig = _clean(f.get("ig_handle")).lstrip("@")
    bits = [f'"{name}"' if name else "", f"instagram {ig}" if ig else ""]
    query = " ".join(b for b in bits if b).strip()
    if not query:
        return None
    return f"{query} location city"
