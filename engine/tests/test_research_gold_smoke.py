"""Research smoke-eval: run the adapter against the niche gold set (bead 1mk.4).

DB-free, hermetic. Replays every example in
``evals/gold/research-niche-smoke.jsonl`` through the router (fixture provider)
and asserts the labeled expectation holds — the "gold-set-proven" depth bar at
SMOKE level. The real relevance/recall holdout is Phase-2 ``rvy``; this proves
the adapter's niche-fit, thin-data, and false-positive behavior deterministically.

Scope note: the fixture provider stands in for the live Firecrawl/Foreplay
providers, so this gates the *adapter's* relevance contract, not live-source
recall (that lands with the eng-wired providers + the rvy holdout).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research import Channel, FixtureProvider, ResearchQuery, ResearchRouter

_GOLD = Path(__file__).resolve().parents[2] / "evals" / "gold" / "research-niche-smoke.jsonl"


def _cases() -> list[dict]:
    return [json.loads(line) for line in _GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_gold_set_meets_hard_negative_floor():
    cases = _cases()
    hard = [c for c in cases if c["label_payload"].get("expect") == "empty" or c.get("hard")]
    # Protocol floor: >=30% hard cases, absolute >=10 (we run a 12-case smoke set,
    # so the percentage floor governs here).
    assert len(cases) >= 12
    assert len(hard) / len(cases) >= 0.30, f"hard-negative floor not met: {len(hard)}/{len(cases)}"


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_research_smoke_case(case):
    inp = case["input"]
    label = case["label_payload"]
    router = ResearchRouter([FixtureProvider()])
    result = router.gather(
        ResearchQuery(
            intent=inp["intent"],
            niche=inp.get("niche", ""),
            seed_terms=tuple(inp.get("seed_terms", ())),
            competitor=inp.get("competitor"),
            limit=inp.get("limit", 20),
            tenant_id=case["tenant"],
        )
    )

    expect = label["expect"]
    if expect == "empty":
        assert result.is_empty, f"{case['id']}: expected empty"
        if "note_contains" in label:
            assert any(label["note_contains"] in n for n in result.notes)
        return

    if expect == "non_empty_signals":
        assert result.signals
        if label.get("sorted_by_confidence_desc"):
            cs = [s.confidence for s in result.signals]
            assert cs == sorted(cs, reverse=True)
    elif expect == "non_empty_communities":
        assert result.communities
        if label.get("every_community_has_entry_tactic"):
            assert all(c.entry_tactic for c in result.communities)
    elif expect == "non_empty_creatives":
        assert result.creatives
        if label.get("every_creative_has_angle"):
            assert all(c.angle for c in result.creatives)

    if "must_include_channel" in label:
        ch = Channel(label["must_include_channel"])
        present = (
            {s.channel for s in result.signals}
            | {c.channel for c in result.communities}
            | {c.channel for c in result.creatives}
        )
        assert ch in present, f"{case['id']}: missing channel {ch}"

    if label.get("must_flag_low_confidence_false_positive"):
        assert any("false positive" in n for n in result.notes)
        assert any(c.confidence < 0.5 for c in result.creatives)

    if "max_results" in label:
        assert len(result.signals) <= label["max_results"]
