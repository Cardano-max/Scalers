"""Build + provenance tests for the practitioner-wisdom harvest (bead 1mk.9).

DB-free (always runs in CI). The load-bearing guarantee is VERBATIM fidelity:
every emitted ``text`` must appear EXACTLY in its source doc — paraphrase
reintroduces AI tells, which is the whole point of the KB. These tests fail
loudly if the generator ever drifts a sentence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kb.corpus import build_practitioner_wisdom as build_mod

_ENGINE_ROOT = Path(__file__).resolve().parents[1]
_JSONL = _ENGINE_ROOT / "kb" / "corpus" / "practitioner_wisdom.jsonl"

EXPECTED_CATEGORIES = {
    "general", "brand-voice", "hooks-cta", "research", "reply", "outreach", "do", "dont",
}


@pytest.fixture(scope="module")
def entries() -> list[dict]:
    return build_mod.build()


def test_provenance_every_text_is_verbatim_in_source(entries):
    """The whole point: no paraphrase. Each text is a literal source substring."""
    failures = build_mod.verify(entries)
    assert failures == [], "non-verbatim entries:\n" + "\n".join(failures)


def test_all_six_quote_categories_plus_do_dont_present(entries):
    cats = {e["category"] for e in entries}
    assert cats == EXPECTED_CATEGORIES, f"category set drifted: {cats}"


def test_every_entry_is_global_and_partitioned(entries):
    for e in entries:
        assert e["scope"] == "GLOBAL"
        assert e["partition"] == "practitioner-wisdom"
        assert e["text"].strip(), "empty verbatim text"
        assert e["content_hash"], "missing content hash"


def test_kind_classification(entries):
    kinds = {e["kind"] for e in entries}
    assert kinds <= {
        "testimonial", "curated-skill-description", "operator-note", "distilled-rule",
    }
    # The operator's own framing note is captured as an operator-note.
    assert any(e["kind"] == "operator-note" for e in entries)
    # T3's one-line skill summaries are flagged, not passed off as testimonials.
    assert any(e["kind"] == "curated-skill-description" for e in entries)
    # DO/DON'T rules are distilled, and live under the do/dont categories only.
    distilled = [e for e in entries if e["kind"] == "distilled-rule"]
    assert distilled and all(e["category"] in {"do", "dont"} for e in distilled)


def test_embedded_quotes_preserved_intact(entries):
    """A quote containing an inner '...' must keep that inner quote verbatim."""
    kaancata = next(
        e for e in entries if e["text"].startswith("Same for paid media.")
    )
    assert '"this campaign is producing leads, but sales cannot use them"' in kaancata["text"]


def test_non_ascii_kept_verbatim(entries):
    fr = [e for e in entries if e["language"] == "fr"]
    assert fr, "expected the French testimonial"
    assert "synthèses d'entretiens quali/quanti" in fr[0]["text"]


def test_content_hash_is_stable_and_matches_text(entries):
    for e in entries:
        assert e["content_hash"] == build_mod._hash(e["text"])


def test_no_duplicate_rows_within_category(entries):
    keys = [(e["category"], e["content_hash"]) for e in entries]
    assert len(keys) == len(set(keys)), "duplicate (category, content_hash)"


def test_committed_jsonl_matches_a_fresh_build(entries):
    """The checked-in JSONL must equal what the generator produces now, so the
    asset never silently drifts from its source docs."""
    assert _JSONL.exists(), "practitioner_wisdom.jsonl not built/committed"
    on_disk = [json.loads(line) for line in _JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert on_disk == entries, "committed JSONL is stale — re-run build_practitioner_wisdom"
