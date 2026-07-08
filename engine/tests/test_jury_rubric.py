"""Jury hard-fail catalog + RUBRIC anchor loader (AUTON-01 / 4jx.2) — DB-free.

Loads the committed #80 corpus and checks the closed-set + FAIL-SAFE contract: known
codes map to dimensions, soft-caps cap, and an unknown code or catalog_version drift
fails safe to review.
"""

from __future__ import annotations

from autonomy.rubric import (
    EXPECTED_CATALOG_VERSION,
    load_hard_fail_catalog,
    load_rubric_anchors,
    resolve_codes,
)


def test_catalog_loads_closed_set_at_pinned_version():
    cat = load_hard_fail_catalog()
    assert cat.catalog_version == EXPECTED_CATALOG_VERSION == 1
    # the 9 committed codes (5 voice hard-fail, 3 appr hard-fail, 1 appr soft-cap)
    assert "VOICE_HF_BANNED_LEXICON" in cat.codes
    assert "APPR_HF_COMMERCIALIZE_TRAUMA" in cat.codes
    assert "APPR_SC_OUT_OF_SCOPE" in cat.soft_cap_codes
    assert cat.dimension_of("VOICE_HF_BANNED_LEXICON") == "voice"
    assert cat.dimension_of("APPR_HF_COMMERCIALIZE_TRAUMA") == "appr"  # normalized
    assert cat.soft_cap_max["APPR_SC_OUT_OF_SCOPE"] == 0.5  # anchor cap 2/4


def test_resolve_known_hard_fail_code_to_dimension():
    cat = load_hard_fail_catalog()
    res = resolve_codes(["APPR_HF_COMMERCIALIZE_TRAUMA"], catalog=cat, judge_catalog_version=1)
    assert res.hard_fail_dims == frozenset({"appr"}) and not res.fail_safe


def test_resolve_soft_cap_caps_not_floors():
    cat = load_hard_fail_catalog()
    res = resolve_codes(["APPR_SC_OUT_OF_SCOPE"], catalog=cat, judge_catalog_version=1)
    assert res.hard_fail_dims == frozenset() and res.soft_cap == {"appr": 0.5}
    assert not res.fail_safe


def test_unknown_code_fails_safe():
    cat = load_hard_fail_catalog()
    res = resolve_codes(["VOICE_HF_BANNED_LEXICON", "NOT_A_REAL_CODE"], catalog=cat, judge_catalog_version=1)
    assert res.fail_safe and "unknown" in res.reason.lower()


def test_catalog_version_drift_fails_safe():
    cat = load_hard_fail_catalog()
    res = resolve_codes(["VOICE_HF_BANNED_LEXICON"], catalog=cat, judge_catalog_version=2)
    assert res.fail_safe and "version" in res.reason.lower()


def test_no_codes_is_clean():
    cat = load_hard_fail_catalog()
    res = resolve_codes([], catalog=cat, judge_catalog_version=1)
    assert not res.fail_safe and not res.hard_fail_dims and not res.soft_cap


def test_rubric_anchors_load_and_are_all_rubric_split():
    anchors = load_rubric_anchors()
    assert len(anchors) == 31  # 12 canonical (#80) + 19 dual-verified edge-cases (0cf4e4b)
    assert all(a.tenant_id == "ladies8391" for a in anchors)
    # at least one anchor carries hard-fail codes (the off-voice/inappropriate band)
    hf = [a for a in anchors if a.expected.get("hard_fail_codes")]
    assert hf and all(c in load_hard_fail_catalog().codes for a in hf for c in a.expected["hard_fail_codes"])


def test_rubric_anchor_corpus_coverage():
    """The expanded corpus must exemplify the full anchor bands and every catalog code
    (pmm calibration contract, 0cf4e4b) — a regression guard on coverage, not a count."""
    anchors = load_rubric_anchors()
    voice = {a.expected["anchors"].get("voice") for a in anchors if "voice" in a.expected.get("anchors", {})}
    appr = {a.expected["anchors"].get("appropriateness") for a in anchors if "appropriateness" in a.expected.get("anchors", {})}
    assert voice == {0, 1, 2, 3, 4}, f"voice band coverage gap: {sorted(voice)}"
    assert appr == {1, 2, 3, 4}, f"appropriateness band coverage gap: {sorted(appr)}"
    used = {c for a in anchors for c in a.expected.get("hard_fail_codes", []) + a.expected.get("soft_cap_codes", [])}
    assert used == set(load_hard_fail_catalog().codes), f"code coverage != catalog: {used ^ set(load_hard_fail_catalog().codes)}"
