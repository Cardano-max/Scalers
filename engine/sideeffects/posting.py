"""Mock posting publisher behind the exactly-once boundary (POST-03 / a9m.8).

The publish half of the Phase-3 posting slice. Real IG/FB publishing is gated
(Meta app review pending, Phase-6); this proves the **publish boundary** now — an
approved post fires **exactly once** under retry/crash via the Phase-1 idempotency
key + Postgres UNIQUE + outbox/ledger, with **no real Meta call**.

Three pieces:

* :class:`PublishIntent` — the typed outbox payload (ADR Decision 5): what to
  publish where. Mock generates no real asset, so ``media_ref`` is ``None`` and the
  ``media`` spec rides along for the audit trail / the Phase-6 connector.
* :class:`MockPostingConnector` — implements the boundary's
  :class:`~sideeffects.dispatcher.Connector` protocol (``send(key, channel,
  payload)``), the SAME seam the real Meta MCP connector implements in Phase 6
  (drop-in). Idempotent on ``key``; touches **no credentials** and makes **no
  network call**. (The ADR sketches a ``PostingConnector.publish(intent)`` shape;
  it is realized here as ``Connector.send`` carrying a serialized ``PublishIntent``
  — the boundary speaks ``(key, channel, payload)``, so the connector deserializes.)
* :func:`publish_approved_post` — the manual **approve-path** entry. 439 holds
  auto-fire (``harness.hold``); the only way a post publishes in Phase 3 is an
  operator explicitly approving it. This enqueues the approved intent through the
  boundary **in the caller's transaction** (coupling the publish intent to the
  Action→APPROVED advance). It refuses an out-of-spec draft (a failing gate) at the
  boundary, and a double-approve dedupes to a single effect.

The exactly-once machinery itself is unchanged (``sideeffects.boundary`` +
``sideeffects.dispatcher``); this module only adds the posting connector + the
approve seam + the **platform-qualified** key.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from cells.content_brief import Platform
from cells.post_schemas import MediaSpec, PostDraft
from harness.state import Gate
from sideeffects.boundary import EnqueueResult, SideEffectBoundary
from sideeffects.keys import Channel, idempotency_key
from sideeffects.provider import ProviderResult


class OutOfSpecError(ValueError):
    """An approve was requested for a draft with a failing gate.

    The boundary refuses to publish out-of-spec content (edge case: approve on a
    failing-gate Action). Raised by :func:`publish_approved_post` BEFORE any
    enqueue, so an out-of-spec post never reaches the outbox.
    """


class PublishIntent(BaseModel):
    """The typed outbox payload for a post publish (ADR Decision 5).

    ``media_ref`` is ``None`` in Phase-3 mock (no real asset is produced); the
    ``media`` spec + brief ride along for the audit trail and the Phase-6 connector.
    """

    model_config = ConfigDict(frozen=True)

    platform: Platform
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    call_to_action: str = ""
    media: MediaSpec
    media_ref: str | None = None        # real asset id — None for the mock slice
    scheduled_at: str | None = None     # ISO-8601; None = publish now

    @classmethod
    def from_draft(cls, draft: PostDraft, *, scheduled_at: str | None = None) -> "PublishIntent":
        """Build the outbox intent from the a9m.5 draft cell's typed output."""
        return cls(
            platform=draft.platform,
            caption=draft.caption,
            hashtags=list(draft.hashtags),
            call_to_action=draft.call_to_action,
            media=draft.media,
            media_ref=None,
            scheduled_at=scheduled_at,
        )

    def canonical_content(self) -> str:
        """Stable string identifying THIS post's content — the idempotency-key
        material. Deterministic (sorted keys) and **excludes run_id / scheduled_at**
        so a replay or a fresh run of the same approved post derives the SAME key and
        dedupes. Same content ⇒ same logical effect ⇒ publish once.

        Includes the FULL creative (caption, hashtags, CTA, and the whole media spec
        — kind/aspect/duration/brief), not just the media kind: two posts that share
        caption/hashtags/CTA but differ only in the creative brief are DIFFERENT
        posts and must derive DIFFERENT keys, or the second silently dedups away (the
        4hj under-fire class, along the creative axis)."""
        return json.dumps(
            {
                "platform": self.platform.value,
                "caption": self.caption,
                "hashtags": self.hashtags,
                "call_to_action": self.call_to_action,
                "media": {
                    "kind": self.media.kind.value,
                    "aspect_ratio": self.media.aspect_ratio,
                    "duration_s": self.media.duration_s,
                    "brief": self.media.brief,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def to_payload(self) -> dict[str, Any]:
        """Serialize for the outbox ``payload`` jsonb."""
        return self.model_dump(mode="json")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PublishIntent":
        """Reconstruct the intent the connector publishes from the outbox payload.

        Tolerates the enqueue envelope (run_id / tenant_id siblings) by validating
        only the intent fields."""
        fields = set(cls.model_fields)
        return cls.model_validate({k: v for k, v in payload.items() if k in fields})


def posting_target(platform: Platform) -> str:
    """The platform-qualified outbox ``target`` (CustomerAcq-4hj / ADR Decision 5).

    IG and FB are BOTH ``Channel.POSTING``, so an unqualified ``feed`` target would
    derive the SAME key for the same content on both platforms — the second enqueue
    dedups away and one platform silently never posts (a safe under-fire, but wrong).
    Qualifying the target with the platform (``instagram:feed`` vs ``facebook:feed``)
    gives the two posts distinct keys while each stays idempotent under replay."""
    return f"{platform.value}:feed"


def posting_idempotency_key(tenant_id: str, intent: PublishIntent) -> str:
    """Derive the exactly-once key for publishing ``intent`` for ``tenant_id`` —
    platform-qualified so IG and FB never collide."""
    return idempotency_key(
        tenant_id, Channel.POSTING, posting_target(intent.platform), intent.canonical_content()
    )


class MockPostingConnector:
    """Phase-3 mock for the real Meta posting connector (Phase-6 drop-in).

    Implements the boundary's :class:`~sideeffects.dispatcher.Connector` protocol.
    **Idempotent on ``key``**: a second ``send`` with the same key returns the same
    provider result without re-publishing — the property the dispatcher relies on to
    hold exactly-once across a crash in the send→settle window. Makes **no network
    call** and touches **no credentials** (``is_mock`` asserts the path is creds-free).

    Counters distinguish the two quantities the exactly-once tests care about:
    ``call_count`` (distinct effects — must be 1 under crash-retry) vs
    ``invocation_count`` (raw ``send`` calls — may exceed it; the provider deduped).

    NOTE on durability of the dedup: the mock remembers effects in-memory, which
    MODELS a real connector that dedups on a **provider-side idempotency token
    derived from ``key``** (real IG/Gmail provide this; see the ``Connector``
    contract). A single long-lived instance therefore stands in for "the provider
    remembers the token across our crash" — the crash tests reuse one instance for
    exactly that reason (same convention as ``tests.mock_connector.MockConnector``).
    The real Phase-6 connector MUST derive its idempotency token from ``key`` so the
    guarantee survives an actual process restart; an in-memory mock cannot.
    """

    is_mock = True

    def __init__(self) -> None:
        self.invocations: list[str] = []                 # raw send() calls
        self._effects: dict[str, ProviderResult] = {}    # key -> result (deduped effect)
        self.published: list[PublishIntent] = []         # distinct posts, in order

    async def send(self, key: str, channel: str, payload: dict) -> ProviderResult:
        self.invocations.append(key)
        intent = PublishIntent.from_payload(payload)  # validates it's a posting intent
        if key not in self._effects:
            # The (mock) external effect happens here, once, keyed by idempotency key.
            n = len(self._effects) + 1
            ext = f"mock_{intent.platform.value}_{n}"
            self._effects[key] = ProviderResult(
                provider_id=ext,
                external_id=ext,
                deep_link=f"mock://{intent.platform.value}/{ext}",
                extra={
                    "platform": intent.platform.value,
                    "caption_preview": intent.caption[:60],
                    "mock": True,  # audit: this was a mock publish, never a real Meta call
                },
            )
            self.published.append(intent)
        return self._effects[key]

    @property
    def call_count(self) -> int:
        """Distinct external effects — THE exactly-once metric (must be 1)."""
        return len(self._effects)

    @property
    def invocation_count(self) -> int:
        return len(self.invocations)


async def publish_approved_post(
    conn: psycopg.AsyncConnection,
    *,
    tenant_id: str,
    draft: PostDraft,
    gates: Sequence[Gate],
    run_id: str | None = None,
    scheduled_at: str | None = None,
    boundary: SideEffectBoundary | None = None,
) -> EnqueueResult:
    """Enqueue an OPERATOR-APPROVED, in-spec post through the exactly-once boundary.

    The approve path is the explicit human authorization that releases a 439-held
    action — in Phase 3 it is the ONLY way a post publishes (auto is held). Enqueues
    on the caller's ``conn`` so the publish intent commits with the same transaction
    that advances the Action to APPROVED (a crash in that window resumes and
    re-enqueues; the ``ON CONFLICT`` enqueue dedups — no lost or double effect).

    Refuses an out-of-spec draft (any failing gate) with :class:`OutOfSpecError`
    BEFORE enqueueing, so out-of-spec content can never reach the publisher. A
    double-approve returns ``DUPLICATE`` (one outbox row ⇒ one publish). The actual
    publish is performed later by a :class:`~sideeffects.dispatcher.Dispatcher`
    draining the outbox through a :class:`MockPostingConnector`.

    ``gates`` is REQUIRED (no default): the caller MUST pass the Action's
    validator-bank gates (a9m.6) so this boundary actually consults them — defense
    in depth, not fail-open. (An empty sequence means "no deterministic gate fired";
    the operator's approval is still the human sign-off, so that is permitted.)
    """
    failed = [g.name for g in gates if not g.passed]
    if failed:
        raise OutOfSpecError(
            f"cannot publish {draft.platform.value} post for {tenant_id!r}: "
            f"failing gate(s) {failed}"
        )
    intent = PublishIntent.from_draft(draft, scheduled_at=scheduled_at)
    key = posting_idempotency_key(tenant_id, intent)
    payload = {**intent.to_payload(), "tenant_id": tenant_id, "run_id": run_id}
    return await (boundary or SideEffectBoundary()).enqueue(
        conn, key, Channel.POSTING, payload
    )
