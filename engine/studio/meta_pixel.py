"""Meta Pixel / Conversions-API groundwork — audience-commonality signals.

Client direction (PA meeting 2026-07-11): Pixel is a SEPARATE layer from the
per-lead deep research. Where deep research studies ONE known lead, Pixel studies
the WHOLE audience's commonalities — where they go, what they buy/like — so the
campaign can target those habits. The client asked for the groundwork now and a
feasibility check with Muaraf before the next meeting (see
``docs/meta-pixel-feasibility.md``).

This is scaffolding + a deterministic aggregator, NOT a live integration:

  * :func:`summarize_audience_commonalities` — PURE (no network): given
    Pixel/Conversions-API-shaped events, it computes the audience's commonality
    signals (top content categories, referring domains, and interests, by real
    frequency). HONEST: counts only what the events actually carry; an empty or
    signal-less feed returns honest zeros, never an invented interest.
  * :func:`pixel_enabled` / :func:`pixel_settings` — read the tenant's
    ``[meta_pixel]`` config. **No live Pixel/CAPI call fires while disabled** — the
    same disabled-by-default posture the research providers use. Wiring the live
    Conversions API is gated on the Muaraf feasibility sign-off.

The event shape mirrors the Meta Conversions API ``server_event``: an anonymized
visitor key plus ``custom_data`` (content_category / content_name /
source_domain / interests). We never store raw PII here — a visitor is a hashed
key, and commonalities are aggregate counts.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# The custom_data fields we aggregate into audience commonalities. Each maps to a
# "what does this audience have in common" axis the client described.
_COMMONALITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("content_category", "categories"),   # what they browse/buy (Nike, restaurants…)
    ("source_domain", "domains"),         # where they came from / go
    ("interests", "interests"),           # declared/derived interests
)


def _visitor_key(event: dict[str, Any]) -> str | None:
    """The anonymized visitor id for de-duping reach. Prefer an explicit hashed
    key; never a raw email/phone (those are not stored here)."""
    for k in ("visitor_id", "hashed_id", "external_id", "click_id"):
        v = event.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _values(raw: Any) -> list[str]:
    """A custom_data field → list of clean string values (a scalar or a list)."""
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    return [str(x).strip().lower() for x in items if str(x or "").strip()]


def summarize_audience_commonalities(
    events: list[dict[str, Any]] | None, *, top_n: int = 5
) -> dict[str, Any]:
    """Aggregate Pixel/CAPI-shaped events into the audience's commonality signals.

    Returns::

        {"visitors", "events", "categories": [{value, count, share}], "domains":
         [...], "interests": [...], "note"}

    ``visitors`` counts distinct anonymized keys; each commonality axis is the
    top-``top_n`` values by real frequency with the SHARE of visitors that carried
    it. HONEST: a value is only counted from an event that actually carried it; an
    empty / signal-less feed returns zeros and an honest note — never a fabricated
    interest."""
    evts = [e for e in (events or []) if isinstance(e, dict)]
    visitors: set[str] = set()
    # Per-axis: value -> set of visitor keys (so share is over distinct people).
    axis_visitors: dict[str, dict[str, set[str]]] = {out: {} for _, out in _COMMONALITY_FIELDS}
    signal_events = 0

    for i, e in enumerate(evts):
        vkey = _visitor_key(e) or f"__anon_{i}"  # anonymous events still count as reach
        visitors.add(vkey)
        custom = e.get("custom_data") if isinstance(e.get("custom_data"), dict) else e
        carried = False
        for field, out in _COMMONALITY_FIELDS:
            for val in _values(custom.get(field)):
                axis_visitors[out].setdefault(val, set()).add(vkey)
                carried = True
        if carried:
            signal_events += 1

    n_visitors = max(1, len(visitors))

    def _rank(axis: str) -> list[dict[str, Any]]:
        counter = Counter({v: len(keys) for v, keys in axis_visitors[axis].items()})
        return [
            {"value": v, "count": c, "share": round(c / n_visitors, 4)}
            for v, c in counter.most_common(max(1, top_n))
        ]

    out: dict[str, Any] = {
        "visitors": len(visitors),
        "events": len(evts),
        "categories": _rank("categories"),
        "domains": _rank("domains"),
        "interests": _rank("interests"),
        "note": "",
    }
    if not evts:
        out["note"] = "no pixel events on file — nothing to aggregate (not fabricated)"
    elif signal_events == 0:
        out["note"] = (
            "pixel events present but none carried a commonality signal "
            "(content_category / source_domain / interests) — honest zeros"
        )
    else:
        out["note"] = (
            f"aggregated {signal_events} signal-bearing event(s) across "
            f"{len(visitors)} visitor(s)"
        )
    return out


def render_pixel_audience_block(summary: dict[str, Any] | None) -> str:
    """A brief block surfacing the audience's commonalities for targeting, or
    ``""`` when there is no signal. Facts only — real counts/shares, so any copy
    that references them is grounded."""
    if not summary or not (
        summary.get("categories") or summary.get("domains") or summary.get("interests")
    ):
        return ""
    lines = [
        "\nAUDIENCE COMMONALITIES (Meta Pixel) — what this audience has in common, "
        "for targeting. Real aggregate counts across "
        f"{summary.get('visitors', 0)} visitor(s); use to bias targeting, not to "
        "state a claim about any one person.",
    ]
    for axis, label in (("categories", "browse/buy categories"),
                        ("domains", "referring domains"),
                        ("interests", "interests")):
        items = summary.get(axis) or []
        if items:
            top = ", ".join(f"{it['value']} ({int(it['share'] * 100)}%)" for it in items[:5])
            lines.append(f"  - {label}: {top}")
    return "\n".join(lines)


def pixel_settings(tenant_id: str) -> dict[str, Any] | None:
    """The tenant's ``[meta_pixel]`` config as a plain dict, or ``None`` when
    absent/disabled. NEVER resolves the token here (secret stays a ref); a live
    call is gated on ``enabled`` AND the Muaraf feasibility sign-off. Best-effort:
    a broken pack → None."""
    try:
        from config.loader import load_pack

        cfg = getattr(load_pack(tenant_id), "meta_pixel", None)
    except Exception:
        return None
    if cfg is None or not getattr(cfg, "enabled", False):
        return None
    return {
        "enabled": True,
        "pixel_id": cfg.pixel_id,
        # Only whether a token ref is configured — never the value.
        "has_token": cfg.access_token is not None,
    }


def pixel_enabled(tenant_id: str) -> bool:
    """Whether the tenant opts into the Pixel layer. False on no/corrupt pack."""
    return pixel_settings(tenant_id) is not None
