"""Prior-campaign memories are REAL analyst evidence (research quality item).

``lookup_lead`` already returns ``facts["memories"]`` (the lead's ``memories``
rows); these pin that ``analyze_customer`` actually USES them: memory text is part
of the stated-evidence corpus, ``memory`` is a present evidence source only when
memories exist, and the analyst prompt surfaces them. No model, no DB — the LLM
cell is stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace

from studio.psych_profile import (
    INFERRED,
    INSUFFICIENT,
    SRC_MEMORY,
    STATED,
    PsychField,
    PsychLLMOut,
    _build_psych_prompt,
    _present_sources,
    analyze_customer,
)
from studio.reason_history import extract_signals

MEMORY = {
    "text": "Asked twice about bridal flash; replied warmly to the spring campaign.",
    "metadata": {"kind": "campaign"},
}


def _facts(**kw):
    base = {
        "customer_id": "cust_m", "name": "Priya N", "city": "Austin",
        "interests": [], "persona_traits": {}, "tattoo_history": [], "memories": [],
    }
    base.update(kw)
    return base


def _with_llm(monkeypatch, out: PsychLLMOut):
    monkeypatch.setattr(
        "studio.psych_profile._build_psych_cell",
        lambda: SimpleNamespace(run_sync=lambda prompt: out),
    )


def test_stated_read_quoting_a_memory_survives_the_corpus_gate(monkeypatch):
    read = PsychLLMOut(
        intent_strength=PsychField(
            value="moderate", signal=STATED,
            evidence="Asked twice about bridal flash",
            evidence_source=SRC_MEMORY,
        ),
    )
    _with_llm(monkeypatch, read)
    prof = analyze_customer(_facts(memories=[MEMORY]), use_llm=True)
    assert prof.intent_strength.value == "moderate"
    assert prof.intent_strength.signal == STATED
    assert prof.intent_strength.evidence_source == SRC_MEMORY

    # The SAME read with no memory on file dies at the corpus gate — the quote has
    # no first-party surface to trace to (anti-fabrication, unchanged).
    _with_llm(monkeypatch, read)
    prof2 = analyze_customer(_facts(), use_llm=True)
    assert prof2.intent_strength.signal == INSUFFICIENT


def test_inferred_memory_read_requires_memories_present(monkeypatch):
    read = PsychLLMOut(
        trust_level=PsychField(
            value="high", signal=INFERRED,
            evidence="warm reply recorded in prior campaign memory",
            evidence_source=SRC_MEMORY,
        ),
    )
    _with_llm(monkeypatch, read)
    with_mem = analyze_customer(_facts(memories=[MEMORY]), use_llm=True)
    assert with_mem.trust_level.value == "high"

    _with_llm(monkeypatch, read)
    without = analyze_customer(_facts(), use_llm=True)
    assert without.trust_level.signal == INSUFFICIENT


def test_memory_is_a_present_source_only_when_rows_exist():
    signals = extract_signals(None)
    assert SRC_MEMORY in _present_sources(_facts(memories=[MEMORY]), signals, None)
    assert SRC_MEMORY not in _present_sources(_facts(), signals, None)


def test_prompt_surfaces_memories_honestly():
    with_mem = _build_psych_prompt(_facts(memories=[MEMORY]), None, None)
    assert "PRIOR CAMPAIGN MEMORIES" in with_mem
    assert MEMORY["text"] in with_mem
    without = _build_psych_prompt(_facts(), None, None)
    assert "PRIOR CAMPAIGN MEMORIES" not in without
