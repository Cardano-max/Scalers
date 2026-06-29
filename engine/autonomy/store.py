"""Autonomy decision persistence (OBS-02) — the write/read path for the console.

Mirrors ``harness.runstore``: a thin :class:`DecisionStore` protocol with an
in-memory implementation for the demo/tests and a :class:`PostgresDecisionStore`
for the durable path. The console's Observability read API (kkg.4) serves these
rows to the jury card + confidence bar.

Schema (one decision per action, **one row per cross-family judge**):

* ``autonomy_decisions`` — the parent row: pooled confidence, per-channel
  threshold, agreement, route decision, safety verdict, escalation, and the gate
  results as JSONB (``[{label, ok}]``).
* ``autonomy_jury`` — one row per judge per decision (``judge, family, voice,
  safety, appr``), the literal "one row per cross-family judge" the bead asks for
  and the most queryable shape on real PG.

The Phase-5 jury changes only what fills the rows, not their shape.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from autonomy.decision import (
    DecisionRecord,
    Escalation,
    EscKind,
    GateResult,
    JudgeVote,
    SafetyVerdict,
)
from harness.state import RouteDecision


@runtime_checkable
class DecisionStore(Protocol):
    """Thin persistence interface for autonomy decision records."""

    def record_decision(self, record: DecisionRecord) -> None: ...

    def get_decision(self, decision_id: str) -> DecisionRecord | None: ...

    def list_decisions(self, run_id: str) -> list[DecisionRecord]: ...


class InMemoryDecisionStore:
    """In-memory ``DecisionStore`` for the demo and unit tests."""

    def __init__(self) -> None:
        self._by_id: dict[str, DecisionRecord] = {}

    def record_decision(self, record: DecisionRecord) -> None:
        self._by_id[record.decision_id] = record

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        return self._by_id.get(decision_id)

    def list_decisions(self, run_id: str) -> list[DecisionRecord]:
        return [r for r in self._by_id.values() if r.run_id == run_id]


class PostgresDecisionStore:
    """Postgres ``DecisionStore``: parent row + one ``autonomy_jury`` row per judge.

    The parent row and its jury rows are written in one transaction so a decision
    is never half-persisted. psycopg is imported lazily so the in-memory path
    needs no driver installed.
    """

    def __init__(self, conninfo: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._connect = lambda: psycopg.connect(
            conninfo, autocommit=True, row_factory=dict_row
        )

    def setup(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomy_decisions (
                    decision_id       TEXT PRIMARY KEY,
                    run_id            TEXT        NOT NULL,
                    tenant_id         TEXT        NOT NULL,
                    channel           TEXT        NOT NULL,
                    action_kind       TEXT        NOT NULL,
                    pooled_confidence DOUBLE PRECISION NOT NULL,
                    threshold         DOUBLE PRECISION NOT NULL,
                    agreement         DOUBLE PRECISION NOT NULL,
                    decision          TEXT        NOT NULL,
                    safety_verdict    TEXT        NOT NULL,
                    esc_kind          TEXT        NOT NULL,
                    esc_label         TEXT        NOT NULL,
                    gates             JSONB       NOT NULL DEFAULT '[]'::jsonb,
                    self_consistency  DOUBLE PRECISION,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS autonomy_decisions_run_idx
                    ON autonomy_decisions (run_id);
                CREATE INDEX IF NOT EXISTS autonomy_decisions_tenant_idx
                    ON autonomy_decisions (tenant_id);

                CREATE TABLE IF NOT EXISTS autonomy_jury (
                    decision_id TEXT NOT NULL
                        REFERENCES autonomy_decisions (decision_id) ON DELETE CASCADE,
                    judge       TEXT NOT NULL,
                    family      TEXT,
                    voice       DOUBLE PRECISION NOT NULL,
                    safety      DOUBLE PRECISION NOT NULL,
                    appr        DOUBLE PRECISION NOT NULL,
                    reliability_weight DOUBLE PRECISION,
                    voice_hard_fail  BOOLEAN NOT NULL DEFAULT false,
                    safety_hard_fail BOOLEAN NOT NULL DEFAULT false,
                    appr_hard_fail   BOOLEAN NOT NULL DEFAULT false,
                    PRIMARY KEY (decision_id, judge)
                );

                -- bead-439 lift ledger (4jx.8): durable per-channel lift state,
                -- read by BOTH the router and the independent side-effect boundary
                -- (the two HOLD layers can't disagree). A lift = a row; a revert
                -- sets reverted_at. "Currently lifted" = reverted_at IS NULL.
                CREATE TABLE IF NOT EXISTS autonomy_lifts (
                    id              BIGSERIAL PRIMARY KEY,
                    tenant_id       TEXT        NOT NULL,
                    channel         TEXT        NOT NULL,
                    lifted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    lifted_by       TEXT        NOT NULL,
                    eval_metric_ref TEXT,
                    reverted_at     TIMESTAMPTZ,
                    reverted_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS autonomy_lifts_tenant_channel_idx
                    ON autonomy_lifts (tenant_id, channel);
                -- at most ONE active lift per (tenant, channel)
                CREATE UNIQUE INDEX IF NOT EXISTS autonomy_lifts_active_uq
                    ON autonomy_lifts (tenant_id, channel) WHERE reverted_at IS NULL;

                -- Idempotent additive migration for EXISTING clusters (the CREATEs
                -- above are no-ops there, so add the new columns here too).
                ALTER TABLE autonomy_decisions
                    ADD COLUMN IF NOT EXISTS self_consistency DOUBLE PRECISION;
                ALTER TABLE autonomy_jury
                    ADD COLUMN IF NOT EXISTS reliability_weight DOUBLE PRECISION;
                -- Per-dimension hard-fail (independent dimensions). Drop the
                -- earlier single-bool (never written; eng4 reconcile) and add one
                -- flag per dimension to match voice/safety/appr scores.
                ALTER TABLE autonomy_jury DROP COLUMN IF EXISTS hard_fail;
                ALTER TABLE autonomy_jury
                    ADD COLUMN IF NOT EXISTS voice_hard_fail BOOLEAN NOT NULL DEFAULT false;
                ALTER TABLE autonomy_jury
                    ADD COLUMN IF NOT EXISTS safety_hard_fail BOOLEAN NOT NULL DEFAULT false;
                ALTER TABLE autonomy_jury
                    ADD COLUMN IF NOT EXISTS appr_hard_fail BOOLEAN NOT NULL DEFAULT false;
                """
            )

    def record_decision(self, record: DecisionRecord) -> None:
        from psycopg.types.json import Json

        with self._connect() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO autonomy_decisions (
                    decision_id, run_id, tenant_id, channel, action_kind,
                    pooled_confidence, threshold, agreement, self_consistency, decision,
                    safety_verdict, esc_kind, esc_label, gates, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.decision_id,
                    record.run_id,
                    record.tenant_id,
                    record.channel,
                    record.action_kind,
                    record.pooled_confidence,
                    record.threshold,
                    record.agreement,
                    record.self_consistency,
                    record.decision.value,
                    record.safety_verdict.value,
                    record.esc.kind.value,
                    record.esc.label,
                    Json([g.model_dump() for g in record.gates]),
                    record.created_at,
                ),
            )
            for v in record.jury:
                conn.execute(
                    "INSERT INTO autonomy_jury "
                    "(decision_id, judge, family, voice, safety, appr,"
                    " reliability_weight, voice_hard_fail, safety_hard_fail, appr_hard_fail) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        record.decision_id, v.judge, v.family, v.voice, v.safety, v.appr,
                        v.reliability_weight,
                        v.hard_fail_for("voice"), v.hard_fail_for("safety"), v.hard_fail_for("appr"),
                    ),
                )

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM autonomy_decisions WHERE decision_id=%s", (decision_id,)
            ).fetchone()
            if row is None:
                return None
            jury = conn.execute(
                "SELECT judge, family, voice, safety, appr,"
                " reliability_weight FROM autonomy_jury "
                "WHERE decision_id=%s ORDER BY judge",
                (decision_id,),
            ).fetchall()
        return self._to_record(row, jury)

    def list_decisions(self, run_id: str) -> list[DecisionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autonomy_decisions WHERE run_id=%s ORDER BY created_at",
                (run_id,),
            ).fetchall()
            out: list[DecisionRecord] = []
            for row in rows:
                jury = conn.execute(
                    "SELECT judge, family, voice, safety, appr FROM autonomy_jury "
                    "WHERE decision_id=%s ORDER BY judge",
                    (row["decision_id"],),
                ).fetchall()
                out.append(self._to_record(row, jury))
        return out

    @staticmethod
    def _to_record(row: dict[str, Any], jury: list[dict[str, Any]]) -> DecisionRecord:
        return DecisionRecord(
            decision_id=row["decision_id"],
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            channel=row["channel"],
            action_kind=row["action_kind"],
            # reliability_weight is restored; the single per-judge hard_fail column is
            # the queryable "this judge flagged a disqualifier" signal — the WHICH-
            # dimension detail lives on the decision's esc_label, so per-dimension
            # hard-fail is intentionally not re-hydrated onto each JudgeVote here.
            jury=[
                JudgeVote(
                    judge=v["judge"], family=v["family"],
                    voice=v["voice"], safety=v["safety"], appr=v["appr"],
                    reliability_weight=(
                        v["reliability_weight"] if v.get("reliability_weight") is not None else 1.0
                    ),
                )
                for v in jury
            ],
            pooled_confidence=row["pooled_confidence"],
            threshold=row["threshold"],
            agreement=row["agreement"],
            self_consistency=row.get("self_consistency"),
            gates=[GateResult(**g) for g in row["gates"]],
            safety_verdict=SafetyVerdict(row["safety_verdict"]),
            decision=RouteDecision(row["decision"]),
            esc=Escalation(kind=EscKind(row["esc_kind"]), label=row["esc_label"]),
            created_at=row["created_at"].isoformat()
            if hasattr(row["created_at"], "isoformat")
            else str(row["created_at"]),
        )
