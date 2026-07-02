"""Phase-1 end-to-end slice — the §6.5 seam (HARN-INT).

Composes every Phase-1 piece into one deterministic path:

    load_pack ─▶ graph[ Research(code) ─▶ Assemble(typed Cell) ─▶ route ─▶ Enqueue ]
              ─▶ Dispatcher(mock connector)

* **load_pack** (INFRA-04) loads the per-tenant config.
* the **graph** is the hand-built LangGraph harness (HARN-01) with a durable
  checkpointer; its Assemble node runs a real typed **Cell** (HARN-02) whose
  output is schema-validated or fails on a code path — never raw text downstream.
* **route** (HARN-05) is pure code wired as a CONDITIONAL EDGE: auto / review /
  regenerate from the computed confidence, the threshold, the gates, and the
  channel autonomy. Only ``auto`` flows to the Enqueue node.
* the **Enqueue node** writes the side-effect intent through the exactly-once
  boundary (HARN-04). It lives INSIDE the graph on purpose: the graph is never
  durably "done" until the enqueue node has run, so the checkpointer's
  at-least-once node execution + the idempotent ``ON CONFLICT`` enqueue couple
  the outbox intent to the durable state advance — a crash after the state
  advance but before the enqueue cannot lose the effect (it resumes and enqueues;
  a redundant resume dedupes). This realizes boundary.py's "outbox written with
  the state advance" property end to end.
* the **Dispatcher** then drains the outbox, firing the (mock) connector exactly
  once even under retry.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import psycopg
from pydantic_ai.models import KnownModelName, Model

from autonomy.confidence import MIN_PROBES, PROBE_TEMPERATURE, anchored_self_consistency
from autonomy.produce import resolve_channel_policy
from cells.base import CellError
from cells.content_brief import ContentBrief, build_content_brief_cell
from config.loader import load_pack
from config.schema import Channel as PackChannel
from config.schema import TenantPack
from langgraph.checkpoint.memory import InMemorySaver

from harness.graph import END, START, Harness
from harness.nodes import ResearchNode
from harness.hold import DEFAULT_HOLD_REGISTRY, HoldRegistry
from harness.router import DEFAULT_THRESHOLD, route
from harness.state import AssembleOutput, AutonomyMode, Gate, GraphState, RouteDecision
from sideeffects import Channel, idempotency_key
from sideeffects.boundary import EnqueueStatus, SideEffectBoundary
from sideeffects.dispatcher import Connector, Dispatcher

# Pack/platform channel (config.Channel) -> side-effect channel (sideeffects.Channel).
# Two different axes: the per-tenant pack dial is keyed by platform (instagram /
# facebook / gmail) while the outbox is keyed by effect type (posting / outreach /
# engagement). The slice's input is the pack channel — the autonomy source of
# truth (CustomerAcq-2kp) — and this map gives the outbox the right effect bucket.
_SIDE_EFFECT_CHANNEL: dict[PackChannel, Channel] = {
    PackChannel.INSTAGRAM: Channel.POSTING,
    PackChannel.FACEBOOK: Channel.POSTING,
    PackChannel.GMAIL: Channel.OUTREACH,
}


def _assert_channel_map_total(mapping: dict[PackChannel, Channel]) -> None:
    """Fail fast if any ``config.Channel`` lacks a side-effect mapping (CustomerAcq-epq).

    Structural totality on the vvi safety surface: a future-added channel must
    never silently fall through to AUTO. This runs at **import**, so adding a new
    enum value without mapping it breaks the build/test immediately — the totality
    guarantee is structural, not just test-guarded.
    """
    missing = set(PackChannel) - set(mapping)
    if missing:
        raise RuntimeError(
            "config.Channel -> side-effect channel mapping is not total; unmapped: "
            f"{sorted(c.value for c in missing)}. Add them to _SIDE_EFFECT_CHANNEL "
            "(an unmapped channel cannot be auto-delivered and must never route AUTO)."
        )


# Enforce totality at import: a new channel without a mapping fails fast here.
_assert_channel_map_total(_SIDE_EFFECT_CHANNEL)


def _resolve_routing(pack: TenantPack, channel: PackChannel) -> tuple[float, AutonomyMode]:
    """Resolve ``(threshold, autonomy)`` from the pack dial, **fail-closed**.

    A channel with no side-effect target cannot be auto-delivered, so it is forced
    to ``REVIEW`` regardless of the pack dial — it must never route AUTO
    (CustomerAcq-epq, defense-in-depth behind the import-time totality guard). The
    guard makes this unreachable for real channels; this is the belt to its braces.
    """
    threshold, autonomy = resolve_channel_policy(pack, channel)
    if channel not in _SIDE_EFFECT_CHANNEL:
        autonomy = AutonomyMode.REVIEW
    return threshold, autonomy

# Self-consistency probe size for the assemble cell (AUTON-02 / 4jx.3): 1 temp-0
# decision sample + (PROBE_K - 1) temp>0 probe samples. Must be >= MIN_SAMPLES or
# the estimate could never compute and every run would fail safe to review.
PROBE_K = 3


def _confidence_of(state) -> float | None:
    """Read ``confidence`` whether the graph hands us a model or a mapping.

    ``None`` means UNCOMPUTABLE (the probe could not gather enough samples) and is
    returned as-is — callers must fail safe to review, never coerce it to a number
    (a ``None -> 0.0`` coercion would silently AUTO-fire under a zero threshold)."""
    if isinstance(state, dict):
        return state.get("confidence")
    return getattr(state, "confidence", None)


def _brief_signature(brief: ContentBrief) -> str:
    """Reduce a typed brief to a comparable self-consistency signature.

    Whitespace-normalized lowercase caption — the caption IS the draft the slice
    ships, so agreement on it is agreement on the output. Exact-match is the
    hermetic default; the live path can inject a coarser semantic signature once
    the 4jx.4 embedder lands (composes via ``AssembleCellNode(signature=...)``)."""
    return " ".join(brief.caption.lower().split())


def _draft_of(state) -> str:
    assembled = state["assembled"] if isinstance(state, dict) else state.assembled
    return assembled.draft if assembled else ""


def _run_id_of(state) -> str:
    return state["run_id"] if isinstance(state, dict) else state.run_id


class AssembleCellNode:
    """Assemble graph node backed by a real typed Cell (HARN-02 boundary).

    Runs eng2's content-brief cell, which returns a schema- and validator-valid
    ``ContentBrief`` or raises ``CellError``. The validated brief is mapped into
    the harness's typed ``AssembleOutput`` so only typed state flows downstream.

    **Confidence is COMPUTED (AUTON-02 / 4jx.3), not hardcoded**: the temp-0
    decision sample is the ANCHOR; ``probe_k - 1`` additional temp>0 samples of the
    same cell probe it, and confidence = the fraction of probes agreeing with the
    anchor (the anchor never votes for itself — the confidence must describe the
    draft that SHIPS, not whatever the probes cluster on). A divergent (unstable)
    generation yields low confidence → the route edge sends it to review; too few
    surviving probes yields ``None`` (uncomputable) → fail safe to review. No
    logprobs anywhere.
    """

    name = "assemble"

    def __init__(
        self,
        model: Model | KnownModelName | None = None,
        *,
        probe_k: int = PROBE_K,
        signature=_brief_signature,
    ) -> None:
        if probe_k - 1 < MIN_PROBES:
            raise ValueError(
                f"probe_k={probe_k} yields {probe_k - 1} probes < MIN_PROBES="
                f"{MIN_PROBES}: the estimate could never compute and every run "
                "would route to review"
            )
        self._cell = build_content_brief_cell()  # temp-0 decision path (pinned)
        # The PROBE is a separate cell at temp>0 (ADR Decision 2): the shipped draft
        # stays deterministic; only the consistency probe samples vary.
        self._probe_cell = build_content_brief_cell(temperature=PROBE_TEMPERATURE)
        self._model = model
        self._probe_k = probe_k
        self._signature = signature

    async def __call__(self, state: GraphState) -> dict:
        research = state.research
        topic = research.topic if research else state.topic
        findings = research.findings if research else []
        prompt = f"Topic: {topic}\nGrounded findings:\n" + "\n".join(
            f"- {f}" for f in findings
        )
        brief: ContentBrief = await self._cell.run(prompt, model=self._model)
        assembled = AssembleOutput(topic=topic, draft=brief.caption)

        # Anchored self-consistency probe: (probe_k - 1) temp>0 samples scored
        # AGAINST the shipped decision sample. A failed probe sample is DROPPED
        # (never fabricated); fewer than MIN_PROBES surviving probes -> confidence
        # None -> the route edge fails safe to review.
        anchor = self._signature(brief)
        probe_signatures = []
        for _ in range(self._probe_k - 1):
            try:
                probe = await self._probe_cell.run(prompt, model=self._model)
            except CellError:
                continue
            probe_signatures.append(self._signature(probe))
        confidence = anchored_self_consistency(anchor, probe_signatures)

        return {
            "assembled": assembled,
            "confidence": confidence,
            "step_log": ["assemble"],
        }


class EnqueueNode:
    """Graph node that writes the side-effect intent to the outbox (HARN-04).

    Deliberately a graph node, NOT a post-graph step: the checkpointer only
    records the run as advanced past here AFTER this node has committed its
    enqueue, so a crash in the state-advance→enqueue window leaves the run
    *unfinished* (it resumes and enqueues) rather than *finished-without-intent*
    (the lost-effect bug). The enqueue is idempotent (``ON CONFLICT``), so a
    resume that re-runs this node never double-enqueues. Derives the key from the
    durable draft, so the same content always maps to the same outbox row.
    """

    name = "enqueue"

    def __init__(self, *, dsn: str, tenant_id: str, channel: Channel, target: str) -> None:
        self._dsn = dsn
        self._tenant_id = tenant_id
        self._channel = channel
        self._target = target

    def key_for(self, draft: str) -> str:
        return idempotency_key(self._tenant_id, self._channel, self._target, draft)

    async def __call__(self, state) -> dict:
        draft = _draft_of(state)
        key = self.key_for(draft)
        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=False)
        try:
            async with conn.transaction():
                await SideEffectBoundary().enqueue(
                    conn, key, self._channel, {"draft": draft, "run_id": _run_id_of(state)}
                )
        finally:
            await conn.close()
        return {"step_log": ["enqueue"]}


def _make_route_edge(threshold: float, gates: Sequence[Gate] | None, autonomy: AutonomyMode):
    """Conditional-edge function: only an ``auto`` decision flows to enqueue.

    An UNCOMPUTABLE confidence (``None`` — the probe couldn't gather enough
    samples) fails safe to review here, explicitly: it must never be coerced to a
    number first, or a zero-threshold channel would auto-fire on "couldn't
    compute" (4jx.3 fail-safe)."""

    def choose(state) -> str:
        confidence = _confidence_of(state)
        if confidence is None:
            return END  # uncomputable -> review; never enqueue
        decision = route(confidence, threshold, gates, autonomy)
        return "enqueue" if decision is RouteDecision.AUTO else END

    return choose


def slice_route(
    pack: TenantPack,
    channel: PackChannel,
    confidence: float,
    gates: Sequence[Gate] | None = None,
    *,
    held: bool = False,
) -> RouteDecision:
    """Route an action using the tenant PACK's autonomy dial (CustomerAcq-2kp).

    The per-tenant ``pack.autonomy_for(channel)`` (mode + threshold) is the source
    of truth — NOT a caller-supplied default. ``resolve_channel_policy`` maps the
    pack's autonomy mode onto the router's and yields the channel threshold, so a
    review-mode channel (e.g. the seed pack's gmail at mode=review/0.9) routes to
    ``review`` even at high confidence, and an auto channel (instagram/facebook at
    0.85) auto-fires once confidence clears its bar. An unmapped channel is
    fail-closed to ``review`` — never AUTO (CustomerAcq-epq). When ``held`` is set
    (bead-439 / CustomerAcq-b3f), HOLD overrides the pack dial entirely — the
    action routes to ``review`` (never AUTO) regardless of confidence or mode.
    """
    if held:
        return route(confidence, DEFAULT_THRESHOLD, gates, AutonomyMode.HOLD)
    threshold, autonomy = _resolve_routing(pack, channel)
    return route(confidence, threshold, gates, autonomy)


def build_slice_graph(
    *,
    dsn: str,
    tenant_id: str,
    assemble_model: Model | KnownModelName | None = None,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
    threshold: float = DEFAULT_THRESHOLD,
    gates: Sequence[Gate] | None = None,
    channel: Channel = Channel.POSTING,
    target: str = "feed",
    checkpointer=None,
    enqueue_node: EnqueueNode | None = None,
    probe_k: int = PROBE_K,
    signature=_brief_signature,
):
    """Build the Phase-1 slice graph: research -> assemble -> route -> [enqueue|END].

    ``checkpointer`` defaults to an in-memory saver; inject the durable Postgres
    checkpointer for crash-resume. ``enqueue_node`` can be overridden (e.g. a
    crash-injecting subclass in tests). ``probe_k``/``signature`` tune the 4jx.3
    self-consistency probe (the live path swaps in a semantic ``signature`` once
    the 4jx.4 embedder lands — until then the exact-match default reads low on
    live temp>0 probes and conservatively routes to review).
    """
    harness = Harness()
    harness.add_node(ResearchNode())
    harness.add_node(AssembleCellNode(assemble_model, probe_k=probe_k, signature=signature))
    harness.add_node(
        enqueue_node
        or EnqueueNode(dsn=dsn, tenant_id=tenant_id, channel=channel, target=target)
    )
    harness.add_edge(START, "research")
    harness.add_edge("research", "assemble")
    harness.add_conditional("assemble", _make_route_edge(threshold, gates, autonomy))
    harness.add_edge("enqueue", END)
    return harness.compile(checkpointer or InMemorySaver())


@dataclass
class SliceResult:
    """The outcome of one end-to-end slice run."""

    pack: TenantPack
    state: GraphState
    decision: RouteDecision
    idempotency_key: str | None = None
    enqueue_status: EnqueueStatus | None = None
    dispatched: int = 0
    steps: list[str] = field(default_factory=list)


async def run_slice(
    *,
    tenant_id: str,
    topic: str,
    dsn: str,
    connector: Connector,
    assemble_model: Model | KnownModelName | None = None,
    run_id: str | None = None,
    channel: PackChannel = PackChannel.INSTAGRAM,
    gates: Sequence[Gate] | None = None,
    target: str = "feed",
    checkpointer=None,
    hold_registry: HoldRegistry = DEFAULT_HOLD_REGISTRY,
    probe_k: int = PROBE_K,
    signature=_brief_signature,
) -> SliceResult:
    """Run the deterministic Phase-1 slice end to end and return what happened.

    Routing uses the per-tenant PACK autonomy dial for ``channel``
    (``pack.autonomy_for(channel)``) as the source of truth — NOT a caller default
    (CustomerAcq-2kp). ``channel`` is a pack/platform channel (instagram / facebook
    / gmail); it is mapped to the side-effect channel for the outbox. The enqueue
    happens INSIDE the graph (durably coupled to the state advance); only an
    ``auto`` decision reaches it. Re-running with the same content derives the same
    idempotency key, so a replay never produces a second effect.
    """
    run_id = run_id or f"slice-{tenant_id}-{topic}"
    pack = load_pack(tenant_id)  # INFRA-04: per-tenant config at run start

    # The pack autonomy dial drives routing (fail-closed: an unmapped channel can
    # never AUTO); map the platform channel to its side-effect bucket for the outbox.
    threshold, autonomy = _resolve_routing(pack, channel)
    # bead-439 (CustomerAcq-b3f): a held tenant/channel never auto-fires. The
    # registry is FAIL-SAFE (held unless explicitly lifted), so HOLD overrides the
    # pack dial in both the graph edge and the final decision below.
    held = hold_registry.is_held(tenant_id, channel.value)
    if held:
        autonomy = AutonomyMode.HOLD
    side_channel = _SIDE_EFFECT_CHANNEL[channel]

    graph = build_slice_graph(
        dsn=dsn,
        tenant_id=tenant_id,
        assemble_model=assemble_model,
        autonomy=autonomy,
        threshold=threshold,
        gates=gates,
        channel=side_channel,
        target=target,
        checkpointer=checkpointer,
        probe_k=probe_k,
        signature=signature,
    )
    state = await graph.run(
        run_id, GraphState(tenant_id=tenant_id, run_id=run_id, topic=topic)
    )

    # Uncomputable confidence (None) fails safe to REVIEW explicitly — never coerced
    # to a number (a None -> 0.0 coercion would AUTO under a zero threshold).
    if state.confidence is None:
        decision = RouteDecision.REVIEW
    else:
        decision = slice_route(pack, channel, state.confidence, gates, held=held)
    result = SliceResult(
        pack=pack, state=state, decision=decision, steps=list(state.step_log)
    )
    if decision is not RouteDecision.AUTO:
        return result

    # The intent was enqueued by the in-graph Enqueue node; drain it now.
    draft = state.assembled.draft if state.assembled else ""
    result.idempotency_key = idempotency_key(tenant_id, side_channel, target, draft)
    result.enqueue_status = EnqueueStatus.ENQUEUED
    result.dispatched = await Dispatcher(dsn, connector).dispatch_pending()
    return result
