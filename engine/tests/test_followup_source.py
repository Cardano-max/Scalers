"""IMPACT r2 (CustomerAcq-tlv.2): reply + outcome capture -> persistent memory loop.

Pins the inbound-signal path that ends Groundhog Day: a REAL customer reply (email
reply / Twilio SMS / IG DM) lands as a genuine ``customer`` turn in
``lead_conversations`` AND as a structured OUTCOME memory
(``replied|booked|objected:<type>|no_response``, verbatim reply) on the ``memories``
table — so the NEXT run's psych profile and dossier reason over what actually
happened after the last send.

Runs against the REAL local Postgres (same convention as
``test_studio_research_memory``) with the DETERMINISTIC embedder so memory mechanics
are exercised hermetically. Honesty pins:

* an inbound whose sender cannot be resolved to a real customer writes NOTHING
  (no fabricated attribution);
* webhook redelivery is idempotent — one turn, one outcome memory row;
* ``no_response`` is recorded WITHOUT a conversation turn (the customer said
  nothing; we never invent a turn);
* the dossier's last-outreach/outcome block is honestly empty when no memory exists.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from kb.embedding import DeterministicEmbedder
from memory import MemoryStore

DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


_require_db()

from proactive.followup_source import (  # noqa: E402
    OUTCOME_BOOKED,
    OUTCOME_NO_RESPONSE,
    OUTCOME_REPLIED,
    capture_inbound,
    classify_outcome,
    is_booked,
    latest_outcome,
    record_no_response,
)
from studio.conversations import (  # noqa: E402
    SPEAKER_CUSTOMER,
    append_turn,
    get_conversation,
    upsert_conversation,
)

_PRICE_REPLY = "I love it but honestly it's a bit pricey for me right now"


def _store() -> MemoryStore:
    return MemoryStore(dsn=DSN, embedder=DeterministicEmbedder())


def _tenant() -> str:
    return "t_tlv2_" + uuid.uuid4().hex[:8]


def _cust() -> str:
    return "cust_tlv2_" + uuid.uuid4().hex[:10]


def _mk_customer(tenant: str, *, email=None, phone=None, ig_handle=None) -> str:
    """Insert one minimal REAL customers row (the resolution target)."""
    cid = _cust()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO customers (id, tenant_id, name, email, phone, ig_handle, "
            "interests, preferred_channels, email_opt_in, sms_opt_in, source) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (cid, tenant, "Test Lead", email, phone, ig_handle,
             [], [], bool(email), bool(phone), "test_tlv2"),
        )
    return cid


def _outcome_memories(store: MemoryStore, tenant: str, cid: str) -> list:
    return [
        m for m in store.list_for_subject(
            tenant_id=tenant, subject_type="customer", subject_id=cid
        )
        if (m.metadata or {}).get("kind") == "outcome"
    ]


# ── outcome classification (pure, deterministic, grounded) ─────────────────── #


def test_classify_outcome_booked() -> None:
    assert classify_outcome("Yes please, book me in for Friday!") == OUTCOME_BOOKED


def test_classify_outcome_objected_carries_the_objection_type() -> None:
    assert classify_outcome(_PRICE_REPLY) == "objected:price"


def test_classify_outcome_plain_reply() -> None:
    assert classify_outcome("Thanks, I'll have a look this evening.") == OUTCOME_REPLIED


def test_classify_outcome_booking_wins_over_payment_phrase() -> None:
    # "deposit" is a payment-objection phrase, but an explicit booking confirmation
    # is the stronger, more specific signal — the customer converted.
    assert classify_outcome("Book me in for Saturday, deposit sent!") == OUTCOME_BOOKED


# ── conversations.append_turn (append, never replace) ──────────────────────── #


def test_append_turn_creates_the_row_when_missing() -> None:
    tenant, cid = _tenant(), _cust()
    conv_id, appended = append_turn(
        tenant, cid, _PRICE_REPLY, channel="gmail", source="inbound-email", dsn=DSN
    )
    assert conv_id and appended is True
    conv = get_conversation(tenant, cid, dsn=DSN)
    assert conv is not None
    assert conv["turns"] == [{"speaker": SPEAKER_CUSTOMER, "text": _PRICE_REPLY}]
    assert conv["source"] == "inbound-email"


def test_append_turn_preserves_existing_turns() -> None:
    tenant, cid = _tenant(), _cust()
    upsert_conversation(
        tenant, cid,
        [{"speaker": "studio", "text": "We miss you!"},
         {"speaker": "customer", "text": "Who is this?"}],
        channel="sms", source="upload", dsn=DSN,
    )
    _, appended = append_turn(tenant, cid, _PRICE_REPLY, dsn=DSN)
    assert appended is True
    conv = get_conversation(tenant, cid, dsn=DSN)
    assert [t["text"] for t in conv["turns"]] == [
        "We miss you!", "Who is this?", _PRICE_REPLY,
    ]
    assert conv["turns"][-1]["speaker"] == SPEAKER_CUSTOMER


def test_append_turn_dedupes_exact_redelivery() -> None:
    tenant, cid = _tenant(), _cust()
    append_turn(tenant, cid, _PRICE_REPLY, dsn=DSN)
    _, appended = append_turn(tenant, cid, _PRICE_REPLY, dsn=DSN)
    assert appended is False
    conv = get_conversation(tenant, cid, dsn=DSN)
    assert len(conv["turns"]) == 1


def test_append_turn_rejects_empty_text() -> None:
    with pytest.raises(ValueError):
        append_turn(_tenant(), _cust(), "   ", dsn=DSN)


# ── capture_inbound: turn + outcome memory in one real path ────────────────── #


def test_capture_inbound_appends_turn_and_writes_outcome_memory() -> None:
    tenant, store = _tenant(), _store()
    cid = _mk_customer(tenant, email="cap.tester@example.com")
    result = capture_inbound(
        tenant, text=_PRICE_REPLY, channel="gmail", customer_id=cid,
        memory_store=store, dsn=DSN,
    )
    assert result is not None
    assert result.customer_id == cid
    assert result.outcome == "objected:price"
    assert result.turn_appended is True

    conv = get_conversation(tenant, cid, dsn=DSN)
    assert conv["turns"][-1] == {"speaker": SPEAKER_CUSTOMER, "text": _PRICE_REPLY}

    mems = _outcome_memories(store, tenant, cid)
    assert len(mems) == 1
    meta = mems[0].metadata
    assert meta["outcome"] == "objected:price"
    assert meta["channel"] == "gmail"
    assert meta["verbatim"] == _PRICE_REPLY
    assert _PRICE_REPLY in mems[0].text  # verbatim reply carried in the memory text


def test_capture_inbound_resolves_customer_by_email_case_insensitive() -> None:
    tenant, store = _tenant(), _store()
    cid = _mk_customer(tenant, email="reply.tester@example.com")
    result = capture_inbound(
        tenant, text="Thanks, sounds great.", channel="gmail",
        email="Reply.Tester@Example.COM", memory_store=store, dsn=DSN,
    )
    assert result is not None and result.customer_id == cid
    assert result.outcome == OUTCOME_REPLIED


def test_capture_inbound_resolves_customer_by_phone_and_handle() -> None:
    tenant, store = _tenant(), _store()
    cid_p = _mk_customer(tenant, phone="+15551230001")
    cid_h = _mk_customer(tenant, ig_handle="@ink.fan")
    by_phone = capture_inbound(
        tenant, text="ok!", channel="sms", phone="+15551230001",
        memory_store=store, dsn=DSN,
    )
    by_handle = capture_inbound(
        tenant, text="love this", channel="instagram", ig_handle="@ink.fan",
        memory_store=store, dsn=DSN,
    )
    assert by_phone is not None and by_phone.customer_id == cid_p
    assert by_handle is not None and by_handle.customer_id == cid_h


def test_capture_inbound_unresolved_sender_writes_nothing() -> None:
    tenant, store = _tenant(), _store()
    result = capture_inbound(
        tenant, text="hello?", channel="gmail", email="stranger@nowhere.example",
        memory_store=store, dsn=DSN,
    )
    assert result is None
    with psycopg.connect(DSN) as conn:
        n_conv = conn.execute(
            "SELECT count(*) FROM lead_conversations WHERE tenant_id = %s", (tenant,)
        ).fetchone()[0]
        n_mem = conn.execute(
            "SELECT count(*) FROM memories WHERE tenant_id = %s", (tenant,)
        ).fetchone()[0]
    assert (n_conv, n_mem) == (0, 0)


def test_capture_inbound_redelivery_is_idempotent() -> None:
    tenant, store = _tenant(), _store()
    cid = _mk_customer(tenant, email="redeliver.tester@example.com")
    first = capture_inbound(
        tenant, text=_PRICE_REPLY, channel="gmail", customer_id=cid,
        memory_store=store, dsn=DSN,
    )
    second = capture_inbound(
        tenant, text=_PRICE_REPLY, channel="gmail", customer_id=cid,
        memory_store=store, dsn=DSN,
    )
    assert first.turn_appended is True and second.turn_appended is False
    conv = get_conversation(tenant, cid, dsn=DSN)
    assert len(conv["turns"]) == 1
    assert len(_outcome_memories(store, tenant, cid)) == 1


def test_capture_inbound_rejects_empty_text() -> None:
    with pytest.raises(ValueError):
        capture_inbound(_tenant(), text="", channel="gmail", customer_id=_cust(),
                        memory_store=_store(), dsn=DSN)


# ── no_response: outcome memory WITHOUT an invented turn ───────────────────── #


def test_record_no_response_writes_outcome_memory_without_a_turn() -> None:
    tenant, cid, store = _tenant(), _cust(), _store()
    mem_id = record_no_response(
        tenant, cid, channel="gmail", run_id="run_x", memory_store=store, dsn=DSN
    )
    assert mem_id
    mems = _outcome_memories(store, tenant, cid)
    assert len(mems) == 1
    assert mems[0].metadata["outcome"] == OUTCOME_NO_RESPONSE
    assert mems[0].metadata["run_id"] == "run_x"
    # The customer said nothing — we never fabricate a conversation turn.
    assert get_conversation(tenant, cid, dsn=DSN) is None


# ── outcome readers over facts['memories'] (the next-run feed) ─────────────── #

_OUTCOME_MEM = {
    "text": 'Outcome of last gmail outreach: objected:price. Customer replied: "too pricey".',
    "metadata": {"kind": "outcome", "outcome": "objected:price", "channel": "gmail",
                 "verbatim": "too pricey"},
}
_OUTREACH_MEM = {
    "text": "Staged gmail outreach to Test Lead for goal 'win back'. Grounded on: name=Test Lead.",
    "metadata": {"kind": "outreach", "channel": "gmail", "run_id": "run_1"},
}
_BOOKED_MEM = {
    "text": 'Outcome of last sms outreach: booked. Customer replied: "book me in!".',
    "metadata": {"kind": "outcome", "outcome": "booked", "channel": "sms",
                 "verbatim": "book me in!"},
}


def test_latest_outcome_reads_newest_outcome_row_only() -> None:
    # facts['memories'] shape: newest-first list of {text, metadata} dicts.
    got = latest_outcome([_OUTCOME_MEM, _OUTREACH_MEM])
    assert got is not None
    assert got["outcome"] == "objected:price"
    assert got["verbatim"] == "too pricey"


def test_latest_outcome_ignores_outreach_rows_and_empty() -> None:
    assert latest_outcome([_OUTREACH_MEM]) is None
    assert latest_outcome([]) is None
    assert latest_outcome(None) is None


def test_is_booked_flags_only_a_booked_latest_outcome() -> None:
    assert is_booked([_BOOKED_MEM, _OUTCOME_MEM]) is True
    assert is_booked([_OUTCOME_MEM, _BOOKED_MEM]) is False  # newer outcome supersedes
    assert is_booked([_OUTREACH_MEM]) is False


# ── the loop closes: captured reply feeds the NEXT run's psych profile ─────── #


def test_captured_reply_feeds_next_runs_psych_profile() -> None:
    from studio.psych_profile import analyze_customer

    tenant, store = _tenant(), _store()
    cid = _mk_customer(tenant, email="psychfeed.tester@example.com")
    capture_inbound(
        tenant, text=_PRICE_REPLY, channel="gmail", customer_id=cid,
        memory_store=store, dsn=DSN,
    )
    conv = get_conversation(tenant, cid, dsn=DSN)
    facts = {"customer_id": cid, "name": "Test Lead", "persona_traits": {},
             "interests": [], "tattoo_history": []}
    profile = analyze_customer(facts, conv, use_llm=False)
    assert profile.had_conversation is True
    assert profile.primary_objection.value == "price"
    assert profile.primary_objection.signal == "stated"
    # The evidence is the customer's REAL last interaction, verbatim.
    assert profile.primary_objection.evidence in _PRICE_REPLY
    assert "blocked on price" in profile.where_customer_sits


# ── dossier: the 'last outreach + outcome' block ───────────────────────────── #


def test_dossier_cites_last_outreach_and_outcome_from_memories() -> None:
    from studio.dossier import build_dossier

    facts = {"customer_id": "c1", "name": "Test Lead", "persona_traits": {},
             "interests": [], "tattoo_history": [],
             "memories": [_OUTCOME_MEM, _OUTREACH_MEM]}
    d = build_dossier(facts)
    assert d.last_outreach.present
    assert d.last_outreach.value == _OUTREACH_MEM["text"]
    assert d.last_outreach.source == "memory:outreach"
    assert d.last_outcome.present
    assert d.last_outcome.value == "objected:price"
    assert d.last_outcome.source == "memory:outcome"
    assert d.last_outcome_verbatim == "too pricey"
    assert "last_outreach" in d.linked_fields()
    assert "last_outcome" in d.linked_fields()


def test_dossier_last_interaction_block_is_honestly_empty_without_memories() -> None:
    from studio.dossier import build_dossier

    facts = {"customer_id": "c1", "name": "Test Lead", "persona_traits": {},
             "interests": [], "tattoo_history": [], "memories": []}
    d = build_dossier(facts)
    assert not d.last_outreach.present
    assert not d.last_outcome.present
    assert d.last_outcome_verbatim == ""


# ── console endpoint: a simulated inbound drives the REAL capture path e2e ─── #


def _endpoint_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from studio.console_api import mount_console_api

    app = FastAPI()
    mount_console_api(app)
    return TestClient(app)


def test_console_inbound_endpoint_drives_the_real_capture_path(monkeypatch) -> None:
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")  # hermetic memory write
    tenant = _tenant()
    cid = _mk_customer(tenant, email="endpoint.tester@example.com")
    resp = _endpoint_client().post("/studio/inbound", json={
        "tenant_id": tenant, "channel": "gmail", "text": _PRICE_REPLY,
        "email": "Endpoint.Tester@example.com",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_id"] == cid
    assert body["outcome"] == "objected:price"
    assert body["turn_appended"] is True
    conv = get_conversation(tenant, cid, dsn=DSN)
    assert conv["turns"][-1]["text"] == _PRICE_REPLY
    assert len(_outcome_memories(_store(), tenant, cid)) == 1


def test_console_inbound_endpoint_404_on_unknown_sender(monkeypatch) -> None:
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    resp = _endpoint_client().post("/studio/inbound", json={
        "tenant_id": _tenant(), "channel": "gmail", "text": "hello?",
        "email": "stranger@nowhere.example",
    })
    assert resp.status_code == 404


def test_console_inbound_endpoint_422_on_empty_text(monkeypatch) -> None:
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    resp = _endpoint_client().post("/studio/inbound", json={
        "tenant_id": _tenant(), "channel": "gmail", "text": "",
        "customer_id": "cust_x",
    })
    assert resp.status_code == 422


# ── qa1 FAIL regressions (tlv.2 rework): the classifier must not lie ────────── #
# Every probe below was executed by qa1 against the shipped classifier and produced
# a WRONG durable memory. Each is pinned here so the false signal can never return.


def test_qa_interest_is_never_booked() -> None:
    # "i'm in" matched inside "interested" (unanchored substring) -> false BOOKED
    # -> warmest leads routed permanently OFF outreach.
    assert classify_outcome("I'm interested, tell me more") == OUTCOME_REPLIED
    assert classify_outcome("Im interested! When are you open?") == OUTCOME_REPLIED


def test_qa_location_im_in_is_not_an_acceptance() -> None:
    assert classify_outcome("Can I see your portfolio? Im in Dallas btw") == OUTCOME_REPLIED


def test_qa_see_you_online_is_not_a_booking() -> None:
    assert classify_outcome("see you online") == OUTCOME_REPLIED


def test_qa_negated_booking_is_not_booked() -> None:
    assert classify_outcome("don't book it yet") != OUTCOME_BOOKED


def test_qa_booked_elsewhere_is_not_our_conversion() -> None:
    assert classify_outcome("i booked with another studio last year") != OUTCOME_BOOKED
    assert classify_outcome("Just booked with another artist, thanks anyway") != OUTCOME_BOOKED


def test_qa_booked_up_with_work_is_not_a_conversion() -> None:
    assert classify_outcome("I am booked in with work all month, sorry") != OUTCOME_BOOKED


def test_qa_want_to_book_but_cant_afford_is_a_price_objection() -> None:
    # The exact signal angle-rotation (#3) needs — was falsely BOOKED.
    assert classify_outcome(
        "I want to book but I cannot afford it right now"
    ) == "objected:price"


def test_qa_ready_to_book_is_booked() -> None:
    # Was "replied": vocabulary inconsistency with reason_history._URGENCY_HIGH.
    assert classify_outcome("I am ready to book!") == OUTCOME_BOOKED


def test_qa_curly_apostrophe_booking_is_booked() -> None:
    # iOS default smart quote (U+2019) — real bookings were missed as "replied".
    assert classify_outcome("I’d like to book for Friday") == OUTCOME_BOOKED
    assert classify_outcome("let’s book it") == OUTCOME_BOOKED


def test_qa_payment_commitment_is_not_an_objection() -> None:
    assert classify_outcome("I'll pay the deposit tomorrow") == OUTCOME_REPLIED


def test_qa_booking_inquiry_is_not_a_timing_objection() -> None:
    assert classify_outcome("Can I book right now?") == OUTCOME_REPLIED


def test_qa_price_inquiry_is_a_buying_signal_not_an_objection() -> None:
    assert classify_outcome("How much is the deposit?") == OUTCOME_REPLIED


def test_qa_question_maybe_is_not_an_uncertainty_objection() -> None:
    assert classify_outcome("Maybe Friday works?") == OUTCOME_REPLIED


def test_qa_stated_objection_in_a_statement_still_reads() -> None:
    # The true-positive path must survive the inquiry filter.
    assert classify_outcome(_PRICE_REPLY) == "objected:price"
    assert classify_outcome("I'm nervous, it's my first tattoo.") == "objected:trust"


def test_qa_caller_supplied_customer_id_is_verified_before_any_write() -> None:
    # Phantom-write: a nonexistent customer_id bypassed resolution and wrote a turn
    # + a booked memory for a customer that does not exist. Now: verify, write NOTHING.
    tenant, store = _tenant(), _store()
    ghost = _cust()  # no customers row
    result = capture_inbound(
        tenant, text="book me in!", channel="gmail", customer_id=ghost,
        memory_store=store, dsn=DSN,
    )
    assert result is None
    with psycopg.connect(DSN) as conn:
        n_conv = conn.execute(
            "SELECT count(*) FROM lead_conversations WHERE tenant_id = %s", (tenant,)
        ).fetchone()[0]
        n_mem = conn.execute(
            "SELECT count(*) FROM memories WHERE tenant_id = %s", (tenant,)
        ).fetchone()[0]
    assert (n_conv, n_mem) == (0, 0)


def test_qa_resolve_customer_raises_on_db_error_never_silent_404() -> None:
    # A transient DB error must surface (5xx -> provider retries), never read as
    # "unknown sender" (404 -> provider marks delivered -> permanent signal loss).
    from proactive.followup_source import resolve_customer

    with pytest.raises(Exception):
        resolve_customer("t_x", email="a@b.c",
                         dsn="postgresql://scalers:scalers@localhost:1/void")


def test_qa_endpoint_5xx_on_transient_db_error(monkeypatch) -> None:
    import proactive.followup_source as fs

    def _boom(*a, **k):
        raise RuntimeError("transient DB failure")

    monkeypatch.setattr(fs, "capture_inbound", _boom)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from studio.console_api import mount_console_api

    app = FastAPI()
    mount_console_api(app)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/studio/inbound",
        json={"tenant_id": "t_x", "channel": "gmail", "text": "hi",
              "email": "a@b.c"},
    )
    assert resp.status_code >= 500  # retryable, NOT 404


def test_qa_whitespace_only_text_is_422_not_500(monkeypatch) -> None:
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    resp = _endpoint_client().post("/studio/inbound", json={
        "tenant_id": _tenant(), "channel": "gmail", "text": "   ",
        "customer_id": "cust_x",
    })
    assert resp.status_code == 422


def test_qa_phone_resolution_matches_across_formats() -> None:
    # Twilio delivers E.164; humans store "(555) 123-4567" — must still match.
    tenant, store = _tenant(), _store()
    cid = _mk_customer(tenant, phone="(555) 987-0001")
    result = capture_inbound(
        tenant, text="ok great", channel="sms", phone="+15559870001",
        memory_store=store, dsn=DSN,
    )
    assert result is not None and result.customer_id == cid


def test_qa_out_of_order_redelivery_does_not_duplicate_turns() -> None:
    tenant, cid = _tenant(), _cust()
    append_turn(tenant, cid, "first message", dsn=DSN)
    append_turn(tenant, cid, "second message", dsn=DSN)
    _, appended = append_turn(tenant, cid, "first message", dsn=DSN)  # redelivery of A
    assert appended is False
    conv = get_conversation(tenant, cid, dsn=DSN)
    assert [t["text"] for t in conv["turns"]] == ["first message", "second message"]


def test_qa_record_no_response_never_flips_a_booked_lead() -> None:
    # The fr1.1 sweep must not overwrite a conversion with silence.
    tenant, cid, store = _tenant(), _cust(), _store()
    store.write(
        tenant_id=tenant, subject_type="customer", subject_id=cid,
        text="Inbound gmail reply -> outcome: booked.",
        metadata={"kind": "outcome", "outcome": "booked", "channel": "gmail"},
    )
    mem_id = record_no_response(
        tenant, cid, channel="gmail", memory_store=store, dsn=DSN
    )
    assert mem_id is None  # refused: the lead already converted
    assert is_booked([
        {"text": m.text, "metadata": m.metadata}
        for m in store.list_for_subject(
            tenant_id=tenant, subject_type="customer", subject_id=cid
        )
    ])


def test_qa_dossier_never_pairs_stale_outcome_with_newer_outreach() -> None:
    from studio.dossier import build_dossier

    newer_outreach = {
        "text": "Staged sms outreach (run 2).",
        "metadata": {"kind": "outreach", "channel": "sms", "run_id": "run2"},
    }
    older_outcome = {
        "text": "Inbound gmail reply -> outcome: objected:price.",
        "metadata": {"kind": "outcome", "outcome": "objected:price",
                     "channel": "gmail", "verbatim": "too pricey"},
    }
    facts = {"customer_id": "c1", "name": "Test", "persona_traits": {},
             "interests": [], "tattoo_history": [],
             "memories": [newer_outreach, older_outcome]}  # newest-first
    d = build_dossier(facts)
    assert d.last_outreach.present
    # The newest outreach has NO reply yet — pairing the older outcome with it
    # would misattribute; the block stays honestly empty instead.
    assert not d.last_outcome.present
    assert d.last_outcome_verbatim == ""
