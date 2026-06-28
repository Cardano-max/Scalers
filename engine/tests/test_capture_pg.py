"""Deep-link + engagement capture (kkg.3 / OBS-03) — real Postgres.

Proves the capture mechanism on the executed side-effect record, keyed to the
idempotency key: provider_result + deep_link on success, engagement (thread /
comments / metrics) as it arrives, idempotent under retry, PII redacted, and a
null deep-link stored gracefully. Real URLs land with real tooling (Phase 3/6)
behind the SAME schema. Marked `integration` + skipif(ENGINE_DATABASE_URL).
"""

from __future__ import annotations

import os

import pytest

from sideeffects import Channel, idempotency_key
from sideeffects.boundary import SideEffectBoundary
from sideeffects.capture import capture_engagement, capture_provider_result
from sideeffects.dispatcher import Dispatcher
from sideeffects.provider import ProviderResult
from tests.mock_connector import MockConnector

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

KEY_ARGS = ("nw", Channel.POSTING, "feed", "the captured post")


async def _enqueue(db, key):
    async with db.transaction():
        await SideEffectBoundary().enqueue(db, key, Channel.POSTING, {"text": "hi"})


async def _ledger(db, key):
    cur = await db.execute(
        "SELECT provider_id, deep_link, provider_result, engagement"
        " FROM side_effect_ledger WHERE idempotency_key = %s",
        (key,),
    )
    return await cur.fetchone()


async def test_dispatch_captures_provider_result_and_deep_link(db, dsn):
    """On success the dispatcher records the provider result + deep_link, keyed
    to idem."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    await Dispatcher(dsn, MockConnector()).dispatch_pending()

    provider_id, deep_link, provider_result, _ = await _ledger(db, key)
    assert provider_id == "prov-1"
    assert deep_link == "mock://posting/prov-1"
    assert provider_result["deep_link"] == "mock://posting/prov-1"
    assert provider_result["external_id"] == "ext-1"
    assert provider_result["thread_ref"] == "thread-1"


async def test_null_deep_link_stored_gracefully(db, dsn):
    """A provider with no URL stores deep_link NULL — the console disables the
    link gracefully rather than crashing."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    await Dispatcher(dsn, MockConnector(omit_deep_link=True)).dispatch_pending()

    _, deep_link, provider_result, _ = await _ledger(db, key)
    assert deep_link is None
    assert provider_result["deep_link"] is None


async def test_provider_result_capture_idempotent_under_retry(db, dsn):
    """A redundant dispatch (retry) does not change the captured provider result
    — capture is keyed to idem, no dup."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    d = Dispatcher(dsn, MockConnector())
    await d.dispatch_pending()
    before = await _ledger(db, key)
    await d.dispatch_pending()  # nothing PENDING; no re-capture
    after = await _ledger(db, key)
    assert before == after


async def test_capture_engagement_thread_comments_metrics(db, dsn):
    """Engagement (replies / comments / metrics) is captured keyed to idem."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    await Dispatcher(dsn, MockConnector()).dispatch_pending()

    merged = await capture_engagement(
        dsn, key,
        thread=[{"role": "in", "name": "Sam", "text": "love this"},
                {"role": "out", "text": "thanks!"}],
        comments=[{"name": "Jo", "text": "nice", "autoReplied": True}],
        metrics=[{"label": "likes", "value": 12}, {"label": "reach", "value": 300}],
    )
    assert len(merged["thread"]) == 2
    assert len(merged["comments"]) == 1
    assert {m["label"]: m["value"] for m in merged["metrics"]} == {"likes": 12, "reach": 300}

    _, _, _, engagement = await _ledger(db, key)
    assert engagement["thread"][0]["text"] == "love this"


async def test_engagement_capture_is_idempotent_and_incremental(db, dsn):
    """Async arrival + retry: re-delivering the same event dedups; a genuinely
    new reply appends; a metric updates in place."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    await Dispatcher(dsn, MockConnector()).dispatch_pending()

    await capture_engagement(dsn, key, thread=[{"role": "in", "name": "Sam", "text": "hi"}],
                             metrics=[{"label": "likes", "value": 1}])
    # Same event re-delivered (retry) + a new reply + updated metric.
    merged = await capture_engagement(
        dsn, key,
        thread=[{"role": "in", "name": "Sam", "text": "hi"},      # dup -> not re-added
                {"role": "out", "text": "hello"}],                # new -> appended
        metrics=[{"label": "likes", "value": 5}],                 # same label -> value updated
    )
    assert len(merged["thread"]) == 2  # no duplicate of "hi"
    assert {m["label"]: m["value"] for m in merged["metrics"]} == {"likes": 5}


async def test_engagement_thread_pii_redacted(db, dsn):
    """PII in a thread message is redacted on capture (edge case)."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    await Dispatcher(dsn, MockConnector()).dispatch_pending()

    merged = await capture_engagement(
        dsn, key,
        thread=[{"role": "in", "name": "Sam", "text": "email me at sam@example.com or 555-123-4567"}],
    )
    text = merged["thread"][0]["text"]
    assert "sam@example.com" not in text and "555-123-4567" not in text
    assert "[redacted-email]" in text and "[redacted-phone]" in text


async def test_capture_provider_result_out_of_band(db, dsn):
    """The provider result can also be captured out-of-band (the real-tooling
    path that resolves the authoritative URL after the fact), keyed to idem."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    await Dispatcher(dsn, MockConnector()).dispatch_pending()  # ledger row exists

    await capture_provider_result(
        dsn, key, ProviderResult(provider_id="p9", deep_link="mock://posting/p9", external_id="e9")
    )
    _, deep_link, provider_result, _ = await _ledger(db, key)
    assert deep_link == "mock://posting/p9" and provider_result["external_id"] == "e9"
