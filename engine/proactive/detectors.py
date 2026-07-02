"""Pure detectors for the proactive daily scanner (CustomerAcq-fr1.1).

Each detector is a pure function of ``(today, context) -> list[Opportunity]`` — no
DB, no LLM, no network — so the scan's REASONS are deterministic and testable; the
DB-backed staging (HELD only) lives in the orchestrator, not here.

Provenance: the ``Opportunity`` contract + year/date-qualified idempotency keys are
reimplemented clean from the prior unpushed ``5d25b4f`` slice. Detector #3 (holiday)
is upgraded here: the prior slice used a hand-curated 9-row static table; this uses
the sec-vetted ``holidays`` pkg (federal + state subdivisions) PLUS a smaller,
explicitly-BADGED tattoo-industry overlay for hooks the pkg does not carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

#: Skin Design Tattoo's operating states (blueprint §4.1). Federal holidays repeat
#: across all of them; state-only observances (e.g. Nevada Day) are badged per state.
DEFAULT_SUBDIVISIONS: tuple[str, ...] = ("NV", "CA", "NY", "HI", "TN")

#: Provenance badge for the curated overlay (never conflated with the pkg source).
OVERLAY_BADGE = "tattoo_industry_overlay"


@dataclass(frozen=True)
class Opportunity:
    """A single, provenance-badged reason to reach out. ``source_badge`` names WHERE
    the reason came from so nothing fabricated can masquerade as a real signal."""

    kind: str  # 'holiday' | 'follow_up' | 'artist_special'
    key: str  # date/year-qualified idempotency key (stable per occurrence)
    title: str
    rationale: str
    source_badge: str
    fire_on: date
    lead_days: int  # days from today to fire_on
    archetype_hint: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _tattoo_overlay(start: date, end: date) -> list[tuple[str, date]]:
    """Curated tattoo-industry observances within ``[start, end]`` the ``holidays``
    pkg does not carry. Kept deliberately small and BADGED (not passed off as a
    civic holiday): National Tattoo Day (Jul 17) + every Friday the 13th."""
    out: list[tuple[str, date]] = []
    for offset in range((end - start).days + 1):
        d = start + timedelta(days=offset)
        if d.month == 7 and d.day == 17:
            out.append(("National Tattoo Day", d))
        if d.day == 13 and d.weekday() == 4:  # Mon=0 .. Fri=4
            out.append(("Friday the 13th", d))
    return out


def holiday_opportunities(
    today: date,
    *,
    subdivisions: tuple[str, ...] = DEFAULT_SUBDIVISIONS,
    window_days: int = 21,
) -> list[Opportunity]:
    """Upcoming public holidays + curated tattoo observances within ``window_days``.

    Federal holidays (present in the base US calendar) are badged
    ``us_holidays:federal``; a holiday that appears only under a state subdivision is
    badged ``us_holidays:<STATE>``. Overlay hooks are badged ``tattoo_industry_overlay``.
    A holiday shared across states surfaces ONCE (deduped by date+name).
    """
    import holidays

    start = today
    end = today + timedelta(days=window_days)
    years = sorted({start.year, end.year})

    federal = holidays.US(years=years)  # nationwide observances (no subdiv)

    # date+name -> (badge, set-of-states) so a shared federal holiday dedupes to one.
    picked: dict[tuple[date, str], tuple[str, list[str]]] = {}
    for subdiv in subdivisions:
        cal = holidays.US(subdiv=subdiv, years=years)
        for d, name in cal.items():
            if not (start <= d <= end):
                continue
            slot = (d, name)
            if d in federal and federal.get(d) == name:
                picked.setdefault(slot, ("us_holidays:federal", []))
            else:
                badge, states = picked.setdefault(slot, (f"us_holidays:{subdiv}", []))
                if subdiv not in states:
                    states.append(subdiv)

    opps: list[Opportunity] = []
    for (d, name), (badge, states) in picked.items():
        facts: dict[str, Any] = {"holiday": name}
        if states:
            facts["states"] = states
        opps.append(
            Opportunity(
                kind="holiday",
                key=f"holiday:{_slug(name)}:{d.isoformat()}",
                title=f"{name} outreach",
                rationale=(
                    f"{name} lands {(d - today).days} day(s) out — a timely, on-brand "
                    "reason to reach out before the date."
                ),
                source_badge=badge,
                fire_on=d,
                lead_days=(d - today).days,
                archetype_hint="holiday",
                facts=facts,
            )
        )

    for name, d in _tattoo_overlay(start, end):
        opps.append(
            Opportunity(
                kind="holiday",
                key=f"holiday:{_slug(name)}:{d.isoformat()}",
                title=f"{name} outreach",
                rationale=(
                    f"{name} ({d.isoformat()}) — a tattoo-culture hook the studio's "
                    "audience recognizes; curated, not a civic holiday."
                ),
                source_badge=OVERLAY_BADGE,
                fire_on=d,
                lead_days=(d - today).days,
                archetype_hint="holiday",
                facts={"overlay": name},
            )
        )

    opps.sort(key=lambda o: (o.fire_on, o.source_badge, o.key))
    return opps
