"""Regression: the psych analyst composes with the message-source ConversationThread.

Before this fix, ``analyze_customer`` could not consume a ``ConversationThread`` (the
adapter's own contract) — it tried to iterate the dataclass and raised, so the analyst
FAILED for exactly the leads that HAD a conversation (the most important ones), silently
degrading them to insufficient-signal. This proves the analyst now normalizes a
ConversationThread to its turns and grounds the read in the customer's own words.
"""

from __future__ import annotations

from studio.adapters.message_source import ConversationThread
from studio.psych_profile import analyze_customer

# A real-shaped price-objection thread ({speaker,text} turns, as the store returns).
_TURNS = [
    {"speaker": "customer", "text": "Hi, I wanted a small floral tattoo on my wrist."},
    {"speaker": "studio", "text": "Lovely! Fine-line or bold?"},
    {"speaker": "customer", "text": "Fine-line, simple. How much?"},
    {"speaker": "studio", "text": "Usually around $120-$180 for a small wrist piece."},
    {"speaker": "customer", "text": "I like it but maybe later, short on budget right now."},
]

_FACTS = {"customer_id": "cust_x", "name": "Sarah", "interests": ["fine-line", "floral"]}


def test_analyze_customer_consumes_conversation_thread_object() -> None:
    thread = ConversationThread(customer_id="cust_x", turns=_TURNS, source="db")
    # Must NOT raise on a ConversationThread (the adapter contract) and must read it.
    profile = analyze_customer(_FACTS, thread, use_llm=False)
    assert profile.had_conversation is True
    # The customer's own words evidence a grounded PRICE objection (stated, not invented).
    assert profile.primary_objection.value == "price"
    assert profile.primary_objection.signal == "stated"


def test_analyze_customer_still_accepts_list_and_none() -> None:
    # The existing list / None contracts are unchanged (no regression to the 13 tests).
    assert analyze_customer(_FACTS, _TURNS, use_llm=False).had_conversation is True
    assert analyze_customer(_FACTS, None, use_llm=False).had_conversation is False
