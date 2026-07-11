"""The CAMPAIGN-TYPE registry — the Phase-A anchor rows (§2, §6 Phase A).

Each row is a versioned :class:`~archetypes.spec.ArchetypeSpec` composed from the
B-block vocabulary. Phase A ships the three highest-value, lowest-gate types
(§6 Phase A) plus the operator-triggered Facebook page-post row:

  1. ``artist_spotlight`` — entity event, IG carousel/Reels, pure organic.
  2. ``holiday``          — cron, curated observances, relevance-gated.
  3. ``win_back``         — behavioral threshold, SMS+email multi-touch (HELD until A2P).
  4. ``facebook_post``    — operator "Facebook campaign" ask, FB Page feed post, organic.

A registered id is the ONLY thing the classifier may emit (Enum-validated against
``REGISTRY`` keys) — it can pick a registered route, never invent one. New types =
new rows here, zero topology change.

``ArchetypeStore`` persists/seeds these to the additive ``archetype_specs`` table
(``CREATE TABLE IF NOT EXISTS`` only). No model calls, no sends.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from archetypes.spec import (
    ARCHETYPE_DDL,
    ArchetypeSpec,
    Channel,
    GateSet,
    StepKind,
    TriggerClass,
    to_row,
)

# The block sets reused below. Every type ends at the route+hold+publish core.
_CORE = {StepKind.B10_ROUTE, StepKind.B11_HOLD, StepKind.B13_PERSIST, StepKind.B14_PUBLISH}
_SHARED_MIDDLE = {StepKind.B6_STRATEGY, StepKind.B7_DRAFT_MANY, StepKind.B8_CRITIQUE, StepKind.B9_JURY}


ARTIST_SPOTLIGHT = ArchetypeSpec(
    id="artist_spotlight",
    version=1,
    trigger=TriggerClass.EVENT,            # new artist onboarded / new style capability
    schedule=None,                         # on-demand (entity event)
    # B1 trigger -> B3 RAG (portfolio/bio) -> B6 angle -> B7 IG carousel/Reels/email
    # -> B8 -> B9 -> B10 -> B11 HELD -> B12 teaser->reveal->CTA -> B14.
    steps_enabled={
        StepKind.B1_TRIGGER, StepKind.B3_RAG, StepKind.B12_SCHEDULE,
        *_SHARED_MIDDLE, *_CORE,
    },
    fanout_key=None,                       # per-channel draft_many only
    fanout_cap=3,                          # IG carousel + Reels + email
    channels=[Channel.IG, Channel.REELS, Channel.EMAIL],
    offer_schema_id=None,                  # spotlight, not an offer
    rubric_id="rubric.artist_spotlight",
    gates=GateSet(
        approval_tier="hold",
        citations_required=False,          # organic, RAG-grounded (no live web fact required)
        consent_required=False,            # organic channels
        skills_allowed=(),                 # 0 REGISTERED skills -> base cells only
    ),
    success_metric="bookings_to_artist + portfolio_views + follower_growth",
)


HOLIDAY = ArchetypeSpec(
    id="holiday",
    version=1,
    trigger=TriggerClass.CRON,             # date approaching, relevance-filtered
    schedule="0 9 * * *",                  # daily 09:00 scan (DBOS.create_schedule later)
    # B1 -> B2 relevance grounding (cited) -> B6 themed angle -> B7 IG/TikTok+email
    # -> B8 tone/sensitivity -> B9 -> B10 -> B11 HELD -> B14.
    steps_enabled={
        StepKind.B1_TRIGGER, StepKind.B2_ENRICH,
        *_SHARED_MIDDLE, *_CORE,
    },
    fanout_key=None,
    fanout_cap=3,                          # IG + TikTok + email
    channels=[Channel.IG, Channel.TIKTOK, Channel.EMAIL],
    offer_schema_id=None,
    rubric_id="rubric.holiday",
    gates=GateSet(
        approval_tier="hold",
        citations_required=True,           # external relevance facts -> Firecrawl-gated
        consent_required=False,
        skills_allowed=(),
    ),
    success_metric="engagement_reach + redemption_if_offer_attached",
)


WIN_BACK = ArchetypeSpec(
    id="win_back",
    version=1,
    trigger=TriggerClass.EVENT,            # behavioral threshold (days-since-visit 30/60/90)
    schedule=None,                         # woken by the behavioral signal
    # B1 -> B4 lapsed/RFM -> B5 suppress+consent -> B6 value-scaled offer -> B7 SMS+email
    # -> B8 -> B9 -> B10 -> B11 HELD -> B12 30/60/90 durable touches -> B14.
    steps_enabled={
        StepKind.B1_TRIGGER, StepKind.B4_SEGMENT, StepKind.B5_CONSENT,
        StepKind.B12_SCHEDULE,
        *_SHARED_MIDDLE, *_CORE,
    },
    fanout_key=None,
    fanout_cap=2,                          # SMS + email
    channels=[Channel.SMS, Channel.EMAIL],
    offer_schema_id="offer.win_back_tiered",
    rubric_id="rubric.win_back",
    gates=GateSet(
        approval_tier="hold",
        citations_required=False,
        consent_required=True,             # SMS stays HELD until A2P 10DLC + TCPA consent rows
        skills_allowed=(),
    ),
    success_metric="reactivation_rate + recovered_revenue",
)


FACEBOOK_POST = ArchetypeSpec(
    id="facebook_post",
    version=1,
    trigger=TriggerClass.OPERATOR,         # studio ask: "Facebook campaign"
    schedule=None,                         # on-demand (operator command)
    # B1 trigger -> B3 RAG (brand voice/portfolio) -> B6 angle -> B7 FB page post
    # (+ email companion) -> B8 -> B9 -> B10 -> B11 HELD -> B14.
    steps_enabled={
        StepKind.B1_TRIGGER, StepKind.B3_RAG,
        *_SHARED_MIDDLE, *_CORE,
    },
    fanout_key=None,                       # per-channel draft_many only
    fanout_cap=2,                          # FB page post + email
    channels=[Channel.FB, Channel.EMAIL],
    offer_schema_id=None,                  # organic page post, not an offer
    rubric_id="rubric.facebook_post",
    gates=GateSet(
        approval_tier="hold",
        citations_required=False,          # organic, RAG-grounded (no live web fact required)
        consent_required=False,            # organic channel (page feed, never DMs)
        skills_allowed=(),                 # 0 REGISTERED skills -> base cells only
    ),
    success_metric="page_engagement_reach + bookings_from_facebook",
)


# The registry keyed by id. The classifier may ONLY emit one of these keys.
REGISTRY: dict[str, ArchetypeSpec] = {
    s.id: s for s in (ARTIST_SPOTLIGHT, HOLIDAY, WIN_BACK, FACEBOOK_POST)
}


# A dynamic Enum of the registered ids — used by the classifier so the model's
# structural output can NEVER be an unregistered string (it is rejected at parse).
ArchetypeId = Enum(  # type: ignore[misc]
    "ArchetypeId", {sid.upper(): sid for sid in REGISTRY}, type=str
)


def get(archetype_id: str) -> ArchetypeSpec:
    """The spec for a registered id, or raise ``KeyError`` (never invents one)."""
    return REGISTRY[archetype_id]


def ids() -> tuple[str, ...]:
    """All registered archetype ids."""
    return tuple(REGISTRY)


class ArchetypeStore:
    """Additive Postgres store for ``archetype_specs`` (seed + read).

    Mirrors :class:`team.store.TeamStore`: lazy psycopg, autocommit dict_row,
    ``CREATE TABLE IF NOT EXISTS`` only. Seeding is idempotent on (id, version).
    """

    def __init__(self, conninfo: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._connect = lambda: psycopg.connect(
            conninfo, autocommit=True, row_factory=dict_row
        )

    def setup(self) -> None:
        """Apply the additive DDL (idempotent)."""
        with self._connect() as conn:
            conn.execute(ARCHETYPE_DDL)

    def seed(self, specs: dict[str, ArchetypeSpec] | None = None) -> int:
        """Upsert the registry rows. Idempotent on (id, version). Returns count."""
        from psycopg.types.json import Json

        rows = (specs or REGISTRY).values()
        n = 0
        with self._connect() as conn:
            for spec in rows:
                r = to_row(spec)
                conn.execute(
                    "INSERT INTO archetype_specs "
                    "(id, version, trigger, schedule, spec, success_metric) "
                    "VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (id, version) DO UPDATE SET "
                    "spec = EXCLUDED.spec, schedule = EXCLUDED.schedule, "
                    "success_metric = EXCLUDED.success_metric",
                    (r["id"], r["version"], r["trigger"], r["schedule"],
                     Json(r["spec"]), r["success_metric"]),
                )
                n += 1
        return n

    def list_specs(self) -> list[dict[str, Any]]:
        """All persisted archetype rows, by id."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT id, version, trigger, schedule, success_metric "
                "FROM archetype_specs ORDER BY id, version"
            ).fetchall()

    def load(self, archetype_id: str, version: int | None = None) -> ArchetypeSpec | None:
        """Reconstruct a typed spec from its persisted JSONB (latest version if None)."""
        with self._connect() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT spec FROM archetype_specs WHERE id=%s "
                    "ORDER BY version DESC LIMIT 1", (archetype_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT spec FROM archetype_specs WHERE id=%s AND version=%s",
                    (archetype_id, version),
                ).fetchone()
        return ArchetypeSpec.model_validate(row["spec"]) if row else None
