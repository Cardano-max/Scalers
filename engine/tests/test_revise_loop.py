"""The critic-driven revise loop + fake-personalization repair + location OSINT.

Three output-quality upgrades (operator order 2026-07-14, "fix fakes / find
location / better outcomes"):

1. ``revise_outreach_draft`` — one bounded rewrite that fixes EXACTLY the
   critic's named issues under a hard anti-fabrication contract.
2. ``_revise_and_rejudge`` — the loop's decision core: a rewrite stages ONLY
   when it judges better; an unjudged rewrite never beats a judged original.
3. ``location_from_verified_research`` — OSINT location from IDENTITY-VERIFIED
   hits only, with the evidence URL + verbatim excerpt riding along.

All hermetic: cells are stubbed, no network, no DB.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from studio.location import location_from_verified_research

# --------------------------------------------------------------------------- #
# location OSINT
# --------------------------------------------------------------------------- #


def test_location_found_in_verified_snippet_with_evidence():
    hits = [
        {"url": "https://example.com/a", "title": "no location here", "snippet": ""},
        {"url": "https://example.com/b", "title": "Derek O — tattoo collector",
         "snippet": "Fine-line enthusiast based in Lake Charles, LA since 2019."},
    ]
    sig = location_from_verified_research(hits)
    assert sig is not None
    assert sig["city"] == "Lake Charles" and sig["state"] == "LA"
    assert sig["display"] == "Lake Charles, LA"
    assert sig["url"] == "https://example.com/b"
    assert "Lake Charles, LA" in sig["excerpt"]


def test_location_never_from_a_non_state_or_empty_hits():
    # "Oslo, NO" — NO is not a US state code; nothing is invented from it.
    assert location_from_verified_research(
        [{"url": "u", "title": "Oslo, NO artist", "snippet": ""}]
    ) is None
    assert location_from_verified_research([]) is None
    assert location_from_verified_research(None) is None


# --------------------------------------------------------------------------- #
# revise_outreach_draft
# --------------------------------------------------------------------------- #

_DRAFT = {
    "channel": "gmail",
    "subject": "Derek - April timing might work after all",
    "draft": "Hi Derek,\nA spot opened up.\nReply STOP to opt out.",
    "grounding": ["angle=timing"],
}


def test_revise_declines_when_llm_off_or_wrong_channel(monkeypatch):
    from studio import customer_research as cr

    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    assert cr.revise_outreach_draft(None, dict(_DRAFT), "weak CTA") is None

    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "1")
    sms = dict(_DRAFT, channel="sms")
    assert cr.revise_outreach_draft(None, sms, "weak CTA") is None
    # Empty critique -> nothing to fix -> no rewrite.
    assert cr.revise_outreach_draft(None, dict(_DRAFT), "   ") is None


def test_revise_rewrites_with_anti_fabrication_contract(monkeypatch):
    from studio import customer_research as cr

    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "1")
    monkeypatch.setattr(cr, "resolve_brand_voice", lambda t: ("", []))

    seen_prompts: list[str] = []

    class _StubCell:
        model = "anthropic:claude-haiku-4-5"

        def run_sync(self, prompt):
            seen_prompts.append(prompt)
            return SimpleNamespace(
                subject="Derek — an April spot just opened",
                body="Hi Derek,\nAn April spot opened up — want it?\nReply STOP to opt out.",
            )

    import cells.copywriter as cw

    monkeypatch.setattr(cw, "build_copywriter_email_cell", lambda **k: _StubCell())
    out = cr.revise_outreach_draft(None, dict(_DRAFT), "CTA is vague; subject tentative")
    assert out is not None
    assert out["subject"] == "Derek — an April spot just opened"
    assert "want it?" in out["draft"]
    assert out["copy_model"] == "anthropic:claude-haiku-4-5"
    # The hard anti-fabrication contract and the critic's issues are IN the prompt.
    p = seen_prompts[0]
    assert "Do NOT introduce any new factual claim" in p
    assert "CTA is vague; subject tentative" in p
    assert _DRAFT["draft"] in p


def test_revise_fails_honest_none_on_cell_error(monkeypatch):
    from studio import customer_research as cr

    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "1")
    monkeypatch.setattr(cr, "resolve_brand_voice", lambda t: ("", []))

    import cells.copywriter as cw

    def _boom(**k):
        raise RuntimeError("cell down")

    monkeypatch.setattr(cw, "build_copywriter_email_cell", _boom)
    assert cr.revise_outreach_draft(None, dict(_DRAFT), "weak CTA") is None


# --------------------------------------------------------------------------- #
# _revise_and_rejudge — the keep/discard decision core
# --------------------------------------------------------------------------- #


class _Critic:
    """Stub critic cell: returns queued (verdict, confidence) per call."""

    def __init__(self, *results):
        self._results = list(results)
        self.prompts: list[str] = []

    def run_sync(self, prompt):
        self.prompts.append(prompt)
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        verdict, conf = item
        return SimpleNamespace(
            verdict=SimpleNamespace(value=verdict), confidence=conf, rationale="r"
        )


_REVISED = {"subject": "S2", "draft": "B2 with CTA", "copy_model": "m2"}


def _rejudge(critic, crit_conf, revise_result):
    from studio.agui import _revise_and_rejudge

    return _revise_and_rejudge(
        critic, dict(_DRAFT), "weak CTA", crit_conf,
        objective="win back", tenant_id=None,
        revise=lambda t, d, c: revise_result,
    )


def test_rejudge_none_when_no_rewrite():
    assert _rejudge(_Critic(), 0.8, None) is None


def test_rewrite_kept_on_approve():
    critic = _Critic(("approve", 0.9))
    out = _rejudge(critic, 0.82, dict(_REVISED))
    assert out["kept"] is True
    assert out["subject"] == "S2" and out["body"] == "B2 with CTA"
    assert out["re_verdict"] == "approve"
    # The re-judgement saw the REWRITE, not the original.
    assert "B2 with CTA" in critic.prompts[0]


def test_rewrite_kept_on_strictly_higher_revise_confidence():
    out = _rejudge(_Critic(("revise", 0.9)), 0.82, dict(_REVISED))
    assert out["kept"] is True


def test_rewrite_discarded_when_not_better():
    out = _rejudge(_Critic(("revise", 0.5)), 0.82, dict(_REVISED))
    assert out["kept"] is False


@pytest.mark.parametrize("crit_conf", [0.82, None])
def test_unjudged_rewrite_never_kept(crit_conf):
    out = _rejudge(_Critic(RuntimeError("critic down")), crit_conf, dict(_REVISED))
    assert out["kept"] is False
    assert out["re_verdict"] is None
