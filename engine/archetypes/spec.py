"""``ArchetypeSpec`` — a campaign TYPE as a typed, versioned config row (§3.1).

This is the heart of the workflow-as-data design: a campaign type is NOT a graph
mutation, it is one Pydantic row. The same fixed LangGraph spine runs every type;
the row selects which pre-declared blocks (B1..B15) are enabled, the trigger class,
the fan-out cap, the channels, the per-type jury rubric, and the gate set. New
campaign types = new ROWS, zero topology change.

Nothing here calls a model or sends anything. It is pure typed data + a couple of
small pure helpers (``compose``, ``ddl``/``upsert_sql``) used by the registry to
seed the additive ``archetype_specs`` table.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Enums — the fixed vocabulary a spec composes from.
# --------------------------------------------------------------------------- #


class StepKind(str, Enum):
    """The reusable LangGraph building blocks (§1, B1..B15).

    A spec's ``steps_enabled`` is a subset of these. The router (``route_archetype``)
    reads the subset to TOGGLE pre-declared spine segments — it never invents a
    node. The B-numbers are stable identifiers (audit + cross-reference to the spec).
    """

    B1_TRIGGER = "B1_trigger"
    B2_ENRICH = "B2_enrich"          # research / live external evidence (cited)
    B3_RAG = "B3_rag"                # tenant knowledge retrieval (pgvector)
    B4_SEGMENT = "B4_segment"        # audience-as-query
    B5_CONSENT = "B5_consent"        # consent / rights / compliance guard
    B6_STRATEGY = "B6_strategy"      # angle + offer params (brand-voice-as-config)
    B7_DRAFT_MANY = "B7_draft_many"  # fan-out to N platform-adapted variants
    B8_CRITIQUE = "B8_critique"      # tool-grounded critic
    B9_JURY = "B9_jury"              # panel -> one confidence
    B10_ROUTE = "B10_route"          # code router (never model)
    B11_HOLD = "B11_hold"            # human-approve / HELD gate
    B12_SCHEDULE = "B12_schedule"    # durable wait (multi-day sequences)
    B13_PERSIST = "B13_persist"      # persist-as-rows / dedupe ledger
    B14_PUBLISH = "B14_publish"      # side-effect boundary (default = HELD draft)
    B15_AMPLIFY = "B15_amplify"      # repost-winner (future)


class TriggerClass(str, Enum):
    """Exactly one entrypoint per campaign (§1 B1)."""

    CRON = "cron"          # calendar / behavioral scan
    EVENT = "event"        # entity event (new artist, booking done)
    POLL = "poll"          # new review / comment / mention
    OPERATOR = "operator"  # Studio command


class Channel(str, Enum):
    """Where a campaign type publishes (default terminal = HELD draft for manual post)."""

    SMS = "sms"
    EMAIL = "email"
    IG = "ig"
    IG_STORIES = "ig_stories"
    REELS = "reels"
    TIKTOK = "tiktok"
    META_PAID = "meta_paid"


# --------------------------------------------------------------------------- #
# Sub-models.
# --------------------------------------------------------------------------- #


class SubgraphRef(BaseModel):
    """A reference to a selectable middle subgraph (§3.4).

    Phase A ships the shared spine only; ``middle_subgraph`` is ``None`` for the 3
    anchor types. Module-5 archetypes (Phase B) set this to ``leadgen_sourcing`` /
    ``ad_mold``. It is a NAME (data), never executable — the compiled subgraph it
    names is fixed, code-reviewed code.
    """

    name: str = Field(description="Registered subgraph name, e.g. 'leadgen_sourcing'.")
    version: int = 1


class GateSet(BaseModel):
    """The gates enforced as code for a campaign type (§5). Never model-overridable.

    These are DECLARATIONS read by the harness; this class does not itself enforce.
    The HELD/approve-first gate is global (every type terminates at B11 HELD); the
    fields here parameterize the per-type specifics the harness checks.
    """

    approval_tier: str = Field(
        default="hold",
        description="Approval posture; Phase A is always 'hold' (approve-first).",
    )
    citations_required: bool = Field(
        default=False,
        description="True if the type runs B2/B3 with external facts (Firecrawl-gated).",
    )
    consent_required: bool = Field(
        default=False,
        description="True if the type sends to a consent-gated channel (SMS/email).",
    )
    skills_allowed: tuple[str, ...] = Field(
        default=(),
        description="Skill ids permitted — REGISTERED-IN-USE only. Empty = base cells.",
    )

    @field_validator("approval_tier")
    @classmethod
    def _approval_is_hold_in_phase_a(cls, v: str) -> str:
        # Phase A safety invariant: nothing auto-sends. The only honest tier is hold.
        if v != "hold":
            raise ValueError(
                f"Phase A approval_tier must be 'hold' (approve-first); got {v!r}"
            )
        return v


# --------------------------------------------------------------------------- #
# The spec.
# --------------------------------------------------------------------------- #


class ArchetypeSpec(BaseModel):
    """One campaign TYPE = one versioned row (§3.1).

    The SAME spine runs every type; this row selects behavior. The model never
    edits topology — it fills content and emits a bounded route label. ``id`` is the
    Enum value the classifier must pick (it can never invent one).
    """

    id: str = Field(description="Stable type id, e.g. 'artist_spotlight'.")
    version: int = 1
    trigger: TriggerClass
    schedule: str | None = Field(
        default=None,
        description="Cron expr -> DBOS.create_schedule (later phase); None = on-demand.",
    )
    steps_enabled: set[StepKind] = Field(
        description="Which of B1..B15 are active. The router toggles spine segments by this."
    )
    fanout_key: str | None = Field(
        default=None,
        description="Per-item fan-out key (e.g. 'leads'); None = per-channel draft_many only.",
    )
    fanout_cap: int = Field(
        default=4, ge=1, le=12,
        description="Hard cost/safety ceiling on B7 Send (per-superstep worker count).",
    )
    middle_subgraph: SubgraphRef | None = Field(
        default=None,
        description="Selectable mid-section; None in Phase A (shared spine only).",
    )
    channels: list[Channel] = Field(description="Publish channels (default terminal = HELD draft).")
    offer_schema_id: str | None = Field(
        default=None,
        description="Id of the typed offer_params schema, approved independently of copy.",
    )
    rubric_id: str = Field(description="Per-type analytic rubric id for the B9 jury.")
    gates: GateSet = Field(default_factory=GateSet)
    success_metric: str = Field(description="The single metric this type is judged on.")

    model_config = {"frozen": False}

    @field_validator("steps_enabled")
    @classmethod
    def _core_blocks_present(cls, v: set[StepKind]) -> set[StepKind]:
        # Every type MUST end at the code router, the HELD gate, and the publish
        # boundary — the approve-first invariant is structural, not optional.
        required = {StepKind.B10_ROUTE, StepKind.B11_HOLD, StepKind.B14_PUBLISH}
        missing = required - v
        if missing:
            raise ValueError(
                "every archetype must enable the route+hold+publish core; missing "
                + ", ".join(sorted(s.value for s in missing))
            )
        return v

    def enabled(self, step: StepKind) -> bool:
        """True if ``step`` is active for this type (read by ``route_archetype``)."""
        return step in self.steps_enabled


# --------------------------------------------------------------------------- #
# Additive persistence (archetype_specs table) — DDL + upsert. CREATE IF NOT
# EXISTS only; never ALTERs or clobbers another module's table.
# --------------------------------------------------------------------------- #

ARCHETYPE_DDL = """
CREATE TABLE IF NOT EXISTS archetype_specs (
    id             TEXT        NOT NULL,
    version        INT         NOT NULL DEFAULT 1,
    trigger        TEXT        NOT NULL,
    schedule       TEXT,
    spec           JSONB       NOT NULL,
    success_metric TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);
CREATE INDEX IF NOT EXISTS archetype_specs_trigger_idx ON archetype_specs (trigger);
"""


def to_row(spec: ArchetypeSpec) -> dict:
    """Flatten a spec to the ``archetype_specs`` row shape (full spec kept in JSONB)."""
    return {
        "id": spec.id,
        "version": spec.version,
        "trigger": spec.trigger.value,
        "schedule": spec.schedule,
        "spec": spec.model_dump(mode="json"),
        "success_metric": spec.success_metric,
    }
