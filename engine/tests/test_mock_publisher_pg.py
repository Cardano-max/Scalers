"""MockPublisher exactly-once tests (POST-03 / a9m.8) — REAL Postgres.

Proves the publish boundary's safety properties against a live cluster (UNIQUE
constraints, FOR UPDATE SKIP LOCKED claiming, crash recovery) using the EXISTING
exactly-once dispatcher with the posting connector:

  * approve an in-spec post -> exactly one mock publish + a SENT ledger row with a
    mock deep_link; NO real API (the connector is a mock);
  * crash between checkpoint/dispatch (after the effect, before settle) -> still
    exactly one effect (the headline crash-injection);
  * double-approve -> one outbox row -> one publish;
  * approve a failing-gate Action -> blocked at the boundary (no outbox row);
  * a failing connector re-queues then FAILs (never a silent drop);
  * the SAME content to IG + FB -> two DISTINCT effects (4hj: platform-qualified key).

Uses the shared `db`/`dsn` fixtures (conftest), which apply the outbox/ledger
schema (02 + 05) and truncate per test.
"""

from __future__ import annotations

import pytest

from cells.content_brief import Platform
from cells.post_schemas import MediaKind, MediaSpec, PostDraft
from harness.state import Gate
from sideeffects.dispatcher import Dispatcher
from sideeffects.posting import (
    MockPostingConnector,
    OutOfSpecError,
    PublishIntent,
    posting_idempotency_key,
    publish_approved_post,
)
from tests.mock_connector import ConnectorCrash

pytestmark = pytest.mark.integration


def _draft(platform: Platform = Platform.INSTAGRAM, caption: str = "fresh ink, fresh start") -> PostDraft:
    return PostDraft(
        platform=platform,
        caption=caption,
        hashtags=["neotraditional", "austintattoo"],
        call_to_action="DM to book your consult",
        media=MediaSpec(kind=MediaKind.IMAGE, aspect_ratio="4:5", brief="healed floral piece"),
    )


_PASS = [Gate(name="aspect_ratio", passed=True), Gate(name="caption_length", passed=True)]


class _CrashAfterSendPostingConnector(MockPostingConnector):
    """Records the (mock) effect then crashes BEFORE settle on the first send per
    key — models 'the post went out, then the process died'. A later drive re-sends,
    the keyed connector dedups, and call_count stays 1."""

    def __init__(self) -> None:
        super().__init__()
        self._crashed: set[str] = set()

    async def send(self, key: str, channel: str, payload: dict):
        result = await super().send(key, channel, payload)  # effect recorded (idempotent)
        if key not in self._crashed:
            self._crashed.add(key)
            raise ConnectorCrash(f"crash after effect for {key!r}")
        return result


class _PoisonPostingConnector(MockPostingConnector):
    """Always raises BEFORE any effect — a genuinely broken publish."""

    async def send(self, key: str, channel: str, payload: dict):
        self.invocations.append(key)
        raise ConnectorCrash(f"poison send for {key!r}")


async def _approve(db, draft, *, tenant="ladies8391", gates=_PASS, run_id="run-1"):
    """Run the approve-path enqueue in the caller's transaction (as the resume would)."""
    async with db.transaction():
        return await publish_approved_post(
            db, tenant_id=tenant, draft=draft, gates=gates, run_id=run_id
        )


async def _outbox(db, key):
    cur = await db.execute(
        "SELECT status, attempts, last_error FROM outbox WHERE idempotency_key = %s", (key,)
    )
    return await cur.fetchall()


async def _ledger(db, key):
    cur = await db.execute(
        "SELECT status, provider_id, deep_link FROM side_effect_ledger WHERE idempotency_key = %s",
        (key,),
    )
    return await cur.fetchall()


# ── happy path: approve -> exactly one publish + ledger + deep_link ───────────


async def test_approve_in_spec_publishes_exactly_once(db, dsn):
    draft = _draft()
    key = posting_idempotency_key("ladies8391", PublishIntent.from_draft(draft))
    res = await _approve(db, draft)
    assert res.status.value == "enqueued"

    connector = MockPostingConnector()
    settled = await Dispatcher(dsn, connector).dispatch_pending()
    assert settled == 1
    assert connector.call_count == 1 and connector.invocation_count == 1
    assert connector.published[0].platform is Platform.INSTAGRAM

    # one SENT outbox row, one SENT ledger row with a MOCK deep_link, no real API.
    assert [r[0] for r in await _outbox(db, key)] == ["SENT"]
    ledger = await _ledger(db, key)
    assert len(ledger) == 1
    status, provider_id, deep_link = ledger[0]
    assert status == "SENT" and provider_id == "mock_instagram_1"
    assert deep_link == "mock://instagram/mock_instagram_1"

    # a redundant drain finds nothing and never re-invokes.
    assert await Dispatcher(dsn, connector).dispatch_pending() == 0
    assert connector.call_count == 1


# ── the headline: crash after send, before settle -> exactly once ────────────


async def test_crash_between_checkpoint_and_dispatch_publishes_once(db, dsn):
    draft = _draft(caption="crash-window post")
    key = posting_idempotency_key("ladies8391", PublishIntent.from_draft(draft))
    await _approve(db, draft)

    # ONE connector instance across both drives on purpose: it models a real
    # connector that dedups on a provider-side idempotency token derived from `key`
    # (which survives our crash). A fresh instance would model a NON-idempotent
    # connector — which correctly double-fires — and is not what exactly-once claims.
    connector = _CrashAfterSendPostingConnector()
    # First drive: effect fires, then crash before settle (caught per-row).
    await Dispatcher(dsn, connector).dispatch_pending()
    assert connector.call_count == 1
    assert [r[0] for r in await _ledger(db, key)] == ["SENDING"]  # durable claim survives

    # Recovery drive (same keyed connector): re-sends, dedups, settles.
    await Dispatcher(dsn, connector).dispatch_pending()
    assert connector.call_count == 1, "exactly one effect despite the crash + retry"
    assert connector.invocation_count == 2  # invoked twice, deduped to one effect
    assert [r[0] for r in await _outbox(db, key)] == ["SENT"]
    assert (await _ledger(db, key))[0][0] == "SENT"


# ── double-approve (operator double-click) -> no second publish ──────────────


async def test_double_approve_does_not_double_publish(db, dsn):
    draft = _draft(caption="double-click post")
    key = posting_idempotency_key("ladies8391", PublishIntent.from_draft(draft))

    first = await _approve(db, draft)
    second = await _approve(db, draft)  # operator clicks approve twice
    assert first.status.value == "enqueued"
    assert second.status.value == "duplicate"
    assert len(await _outbox(db, key)) == 1  # the UNIQUE key kept it to one row

    connector = MockPostingConnector()
    await Dispatcher(dsn, connector).dispatch_pending()
    assert connector.call_count == 1


# ── approve a failing-gate Action -> blocked at the boundary ─────────────────


async def test_approve_failing_gate_blocked_no_outbox_row(db):
    draft = _draft(caption="out of spec post")
    key = posting_idempotency_key("ladies8391", PublishIntent.from_draft(draft))
    bad = [Gate(name="aspect_ratio", passed=True), Gate(name="banned_phrase", passed=False, detail="'slay'")]

    with pytest.raises(OutOfSpecError):
        async with db.transaction():
            await publish_approved_post(db, tenant_id="ladies8391", draft=draft, gates=bad)

    assert await _outbox(db, key) == [], "out-of-spec draft must never reach the outbox"


# ── connector failure -> re-queue then FAIL, never a silent drop ─────────────


async def test_connector_failure_requeues_then_fails(db, dsn):
    draft = _draft(caption="doomed post")
    key = posting_idempotency_key("ladies8391", PublishIntent.from_draft(draft))
    await _approve(db, draft)

    connector = _PoisonPostingConnector()
    dispatcher = Dispatcher(dsn, connector, max_attempts=2)
    await dispatcher.dispatch_pending()  # attempt 1 -> PENDING (re-queued)
    status, attempts, last_error = (await _outbox(db, key))[0]
    assert status == "PENDING" and attempts == 1 and last_error

    await dispatcher.dispatch_pending()  # attempt 2 -> FAILED (cap reached)
    status, attempts, _ = (await _outbox(db, key))[0]
    assert status == "FAILED" and attempts == 2
    assert connector.call_count == 0  # never any effect
    # ledger claim is FAILED, not a stranded SENDING/NULL, and never SENT.
    assert (await _ledger(db, key))[0][0] == "FAILED"


# ── 4hj: same content to IG + FB -> two distinct effects (no collision) ──────


async def test_same_content_ig_and_fb_both_publish(db, dsn):
    ig = _draft(Platform.INSTAGRAM, caption="cross-posted piece")
    fb = _draft(Platform.FACEBOOK, caption="cross-posted piece")  # identical content
    ig_key = posting_idempotency_key("ladies8391", PublishIntent.from_draft(ig))
    fb_key = posting_idempotency_key("ladies8391", PublishIntent.from_draft(fb))
    assert ig_key != fb_key

    await _approve(db, ig)
    await _approve(db, fb)

    connector = MockPostingConnector()
    settled = await Dispatcher(dsn, connector).dispatch_pending()
    assert settled == 2
    assert connector.call_count == 2, "both platforms publish; neither dedups the other away"
    assert {r[0][0] for r in [await _ledger(db, ig_key), await _ledger(db, fb_key)]} == {"SENT"}
    # distinct provider ids, each tagged with its platform (not asserting the global
    # effect counter, which depends on claim order — only the per-platform distinctness).
    ig_pid = (await _ledger(db, ig_key))[0][1]
    fb_pid = (await _ledger(db, fb_key))[0][1]
    assert ig_pid != fb_pid
    assert ig_pid.startswith("mock_instagram_") and fb_pid.startswith("mock_facebook_")
