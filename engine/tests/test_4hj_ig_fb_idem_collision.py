"""FAILING repro for CustomerAcq-4hj (P2, safety): IG+FB idempotency-key collision.

THE BUG
-------
``_SIDE_EFFECT_CHANNEL`` (phase1_slice) collapses BOTH ``PackChannel.INSTAGRAM`` and
``PackChannel.FACEBOOK`` to ``sideeffects.Channel.POSTING``. ``run_slice`` then derives
the idempotency key as::

    idempotency_key(tenant, side_channel, target, draft)
      = f"{tenant}:{side_channel.value}:{target}:{sha256(draft)[:12]}"
      = "ink-studio:posting:feed:<hash>"          # for BOTH IG and FB

The key has tenant, side-channel, target (default ``feed``), and a content hash —
but **no platform segment**. So cross-posting the SAME creative to Instagram and
then Facebook derives the IDENTICAL key. ``outbox`` / ``side_effect_ledger`` are
``UNIQUE(idempotency_key)``, so the second platform's enqueue hits ``ON CONFLICT
DO NOTHING`` and is silently dropped — only ONE platform actually posts.

Direction is SAFE (under-fire, never double-fire), so it is not a Phase-1 blocker
(the demo slice is single-channel-per-call). But the real multi-platform posting
engine (Phase 6) MUST put the platform in the key (e.g. ``target='{platform}:feed'``
or a first-class platform segment), or cross-posting loses one platform's post.

WHAT THIS TEST PROVES
---------------------
Posting identical content to IG then FB on REAL Postgres: today FB's effect is
dropped. The asserts are written for the CORRECT behavior (both effects persist),
so this test is RED now and turns GREEN once the key derivation includes the
platform. It is fix-location-agnostic: it asserts on the resulting key + the
observed effects, not on where the platform segment is added.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from config.schema import Channel as PackChannel
from harness.state import RouteDecision
from phase1_slice import run_slice
from tests.conftest import VALID_BRIEF, tool_model
from tests.mock_connector import MockConnector

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

TENANT = "ink-studio"  # seed pack: instagram=auto/0.85, facebook=auto/0.85
TOPIC = "spring blackwork promo"


def _model():
    return tool_model(VALID_BRIEF)


async def test_ig_and_fb_cross_post_must_both_persist(db, dsn):
    """Cross-post identical content to IG then FB — BOTH effects must persist.

    Permanent regression guard for CustomerAcq-4hj: the idempotency target is
    platform-qualified (``target="{platform}:feed"``, ADR #38 Decision 5), so IG
    and FB derive DISTINCT keys and both cross-posts persist. (Was xfail(strict)
    while the bug was open; the fix flipped it, so the marker is removed.)
    """
    connector = MockConnector()

    # This test exercises idempotency-key derivation on the AUTO/dispatch path,
    # so the bead-439 hold (held-by-default; landed via 4jx13) is lifted
    # EXPLICITLY for the fixture tenant's two posting channels. The process
    # DEFAULT registry stays empty/held — this never weakens the live posture.
    from harness.hold import HoldRegistry

    lifted = HoldRegistry().lift(TENANT, "instagram").lift(TENANT, "facebook")

    ig = await run_slice(
        tenant_id=TENANT, topic=TOPIC, dsn=dsn, connector=connector,
        assemble_model=_model(), channel=PackChannel.INSTAGRAM, target="feed",
        hold_registry=lifted,
    )
    fb = await run_slice(
        tenant_id=TENANT, topic=TOPIC, dsn=dsn, connector=connector,
        assemble_model=_model(), channel=PackChannel.FACEBOOK, target="feed",
        hold_registry=lifted,
    )

    # Both channels are AUTO for this pack, so both reach the enqueue/dispatch path.
    assert ig.decision is RouteDecision.AUTO
    assert fb.decision is RouteDecision.AUTO

    key_ig, key_fb = ig.idempotency_key, fb.idempotency_key

    # CHARACTERIZATION — the root cause: identical content to two DIFFERENT
    # platforms must derive DIFFERENT keys. Today both are
    # "ink-studio:posting:feed:<hash>" (no platform segment) -> they collide.
    assert key_ig != key_fb, (
        f"IG and FB derived the SAME idempotency key ({key_ig!r}) — the platform "
        "is missing from the key, so FB's post is deduped away. Add platform (4hj)."
    )

    # THE SAFETY BUG — both platforms must each post exactly once. Today FB is
    # dropped by the ON CONFLICT enqueue, so only IG posts -> effects == 1.
    assert connector.effects == 2, (
        f"expected 2 distinct side effects (IG + FB), got {connector.effects} — "
        f"FB's cross-post was dropped (ig.dispatched={ig.dispatched}, fb.dispatched={fb.dispatched})."
    )
    assert fb.dispatched == 1, f"FB effect was dropped (dispatched={fb.dispatched})"

    # Ground truth in the ledger: two distinct posting effects for this content.
    with psycopg.connect(dsn) as conn:
        (n_keys,) = conn.execute(
            "SELECT count(DISTINCT idempotency_key) FROM side_effect_ledger"
            " WHERE channel = 'posting' AND status = 'SENT'"
        ).fetchone()
    assert n_keys == 2, f"expected 2 distinct posting ledger rows, found {n_keys}"
