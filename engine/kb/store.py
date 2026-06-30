"""Tenant-isolated data-access layer for the eval KB (KNOW-01, ADR Decision 1-2).

The eval store is OFFLINE (labeling protocol, eval runner, CI gate) — not the
engine hot path — so the DAL is synchronous. Every read REQUIRES a ``tenant_id``
(or an explicit ``scope=GLOBAL`` for metrics): the layer never issues a query
that could return cross-tenant rows. It also sets ``app.current_tenant`` per
operation so the row-level-security policies enforce the same isolation at the
database for non-superuser (``scalers_app``) connections.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg

from kb.embedding import Embedder, default_embedder, to_pgvector
from kb.schema import (
    Direction,
    Engine,
    EvalMetric,
    GoldExample,
    GoldLabel,
    RunKind,
    Scope,
    Split,
)
from kb.voice import Exemplar


def content_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of a canonical-JSON payload (the example natural key)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class KbStore:
    """Synchronous, tenant-scoped access to gold_example / gold_label / eval_metric."""

    def __init__(self, dsn: str, embedder: Embedder | None = None) -> None:
        self._dsn = dsn
        # Default = the REAL semantic embedder (bge-small-en-v1.5); offline runs
        # opt into the deterministic stub via $SCALERS_EMBEDDER (make_embedder).
        self._embedder = embedder or default_embedder()

    @contextmanager
    def _conn(self, tenant_id: str | None) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self._dsn)
        try:
            # Drive the RLS policies (no-op for a superuser, authoritative for
            # scalers_app). Reads/writes also carry an explicit tenant_id filter.
            if tenant_id is not None:
                conn.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant_id,))
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── gold examples ────────────────────────────────────────────────────────

    def upsert_gold_example(
        self,
        *,
        tenant_id: str,
        engine: Engine | str,
        cell: str,
        input: dict[str, Any],
        expected: dict[str, Any] | None = None,
        rubric_dimensions: list[str] | None = None,
        split: Split | str = Split.CALIBRATION,
        label_version: int = 1,
        created_by: str | None = None,
    ) -> str:
        """Insert (or refresh) one example. Idempotent on the natural key
        (tenant, engine, cell, content, label_version) — re-ingest never dups."""
        engine_v = engine.value if isinstance(engine, Engine) else str(engine)
        split_v = split.value if isinstance(split, Split) else str(split)
        chash = content_hash(input)
        embed_text = json.dumps(expected or input, sort_keys=True)
        vec = self._embedder.embed(embed_text)
        if len(vec) != 384:
            raise ValueError(f"embedding dim {len(vec)} != 384 (column is vector(384))")

        with self._conn(tenant_id) as conn:
            row = conn.execute(
                "INSERT INTO gold_example"
                " (tenant_id, engine, cell, input, expected, rubric_dimensions,"
                "  split, label_version, content_hash, embedding, created_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)"
                " ON CONFLICT (tenant_id, engine, cell, content_hash, label_version)"
                " DO UPDATE SET expected = EXCLUDED.expected,"
                "   rubric_dimensions = EXCLUDED.rubric_dimensions,"
                "   split = EXCLUDED.split, embedding = EXCLUDED.embedding"
                " RETURNING id",
                (
                    tenant_id, engine_v, cell, json.dumps(input),
                    json.dumps(expected) if expected is not None else None,
                    rubric_dimensions or [], split_v, label_version, chash,
                    to_pgvector(vec), created_by,
                ),
            ).fetchone()
            return str(row[0])

    def get_gold_set(
        self,
        *,
        tenant_id: str,
        engine: Engine | str,
        label_version: int | None = None,
        cell: str | None = None,
        split: Split | str | None = None,
    ) -> list[GoldExample]:
        """Fetch a gold set for (tenant, engine[, label_version, cell, split]).
        Always tenant-scoped; returns ``[]`` for an empty KB (never raises)."""
        engine_v = engine.value if isinstance(engine, Engine) else str(engine)
        clauses = ["tenant_id = %s", "engine = %s"]
        params: list[Any] = [tenant_id, engine_v]
        if label_version is not None:
            clauses.append("label_version = %s")
            params.append(label_version)
        if cell is not None:
            clauses.append("cell = %s")
            params.append(cell)
        if split is not None:
            clauses.append("split = %s")
            params.append(split.value if isinstance(split, Split) else str(split))

        with self._conn(tenant_id) as conn:
            rows = conn.execute(
                "SELECT id, tenant_id, engine, cell, input, expected, rubric_dimensions,"
                " split, label_version, content_hash, created_at, created_by"
                " FROM gold_example WHERE " + " AND ".join(clauses) + " ORDER BY created_at",
                params,
            ).fetchall()
        return [
            GoldExample(
                id=str(r[0]), tenant_id=r[1], engine=Engine(r[2]), cell=r[3],
                input=r[4], expected=r[5], rubric_dimensions=list(r[6]),
                split=Split(r[7]), label_version=r[8], content_hash=r[9],
                created_at=r[10], created_by=r[11],
            )
            for r in rows
        ]

    # ── gold labels (per rater x dimension) ──────────────────────────────────

    def add_gold_label(
        self,
        *,
        example_id: str,
        tenant_id: str,
        rater_id: str,
        dimension: str,
        label: dict[str, Any],
        label_version: int = 1,
    ) -> str:
        """Add one rater's label for one dimension. Idempotent per
        (example, rater, dimension, version) — a re-rate refreshes, never dups."""
        with self._conn(tenant_id) as conn:
            row = conn.execute(
                "INSERT INTO gold_label"
                " (example_id, tenant_id, rater_id, dimension, label, label_version)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (example_id, rater_id, dimension, label_version)"
                " DO UPDATE SET label = EXCLUDED.label"
                " RETURNING id",
                (example_id, tenant_id, rater_id, dimension, json.dumps(label), label_version),
            ).fetchone()
            return str(row[0])

    def get_labels(self, *, tenant_id: str, example_id: str) -> list[GoldLabel]:
        with self._conn(tenant_id) as conn:
            rows = conn.execute(
                "SELECT id, example_id, tenant_id, rater_id, dimension, label,"
                " label_version, created_at FROM gold_label"
                " WHERE tenant_id = %s AND example_id = %s ORDER BY created_at",
                (tenant_id, example_id),
            ).fetchall()
        return [
            GoldLabel(
                id=str(r[0]), example_id=str(r[1]), tenant_id=r[2], rater_id=r[3],
                dimension=r[4], label=r[5], label_version=r[6], created_at=r[7],
            )
            for r in rows
        ]

    # ── eval metrics (append-only history + gating source of truth) ──────────

    def record_metric(self, metric: EvalMetric) -> str:
        """Append one metric row. ``passed`` is computed from value/dir/threshold
        if not pre-set. GLOBAL-scope metrics may omit tenant_id."""
        if metric.scope is Scope.TENANT and metric.tenant_id is None:
            raise ValueError("TENANT-scoped metric requires tenant_id")
        passed = metric.passed if metric.passed is not None else metric.compute_passed()

        with self._conn(metric.tenant_id) as conn:
            row = conn.execute(
                "INSERT INTO eval_metric"
                " (scope, tenant_id, engine, cell, metric, value, threshold, direction,"
                "  passed, run_kind, label_version, model_pins_hash, prompt_version,"
                "  dataset_hash, git_sha, langfuse_trace_id)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    metric.scope.value, metric.tenant_id, metric.engine, metric.cell,
                    metric.metric, metric.value, metric.threshold,
                    metric.direction.value if metric.direction else None, passed,
                    metric.run_kind.value if metric.run_kind else None,
                    metric.label_version, metric.model_pins_hash, metric.prompt_version,
                    metric.dataset_hash, metric.git_sha, metric.langfuse_trace_id,
                ),
            ).fetchone()
            return str(row[0])

    def get_metrics(
        self,
        *,
        tenant_id: str | None = None,
        scope: Scope | None = None,
        engine: str | None = None,
        cell: str | None = None,
        metric: str | None = None,
        label_version: int | None = None,
    ) -> list[EvalMetric]:
        """Read metric history, tenant-scoped. Requires ``tenant_id`` unless
        ``scope=GLOBAL`` is given explicitly — never returns cross-tenant rows."""
        if tenant_id is None and scope is not Scope.GLOBAL:
            raise ValueError("get_metrics requires tenant_id (or scope=GLOBAL)")
        clauses: list[str] = []
        params: list[Any] = []
        if scope is Scope.GLOBAL:
            clauses.append("scope = 'GLOBAL'")
        else:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        for col, val in (("engine", engine), ("cell", cell), ("metric", metric)):
            if val is not None:
                clauses.append(f"{col} = %s")
                params.append(val)
        if label_version is not None:
            clauses.append("label_version = %s")
            params.append(label_version)

        with self._conn(tenant_id) as conn:
            rows = conn.execute(
                "SELECT id, scope, tenant_id, engine, cell, metric, value, threshold,"
                " direction, passed, run_kind, label_version, model_pins_hash,"
                " prompt_version, dataset_hash, git_sha, langfuse_trace_id, created_at"
                " FROM eval_metric WHERE " + " AND ".join(clauses) + " ORDER BY created_at",
                params,
            ).fetchall()
        return [
            EvalMetric(
                id=str(r[0]), scope=Scope(r[1]), tenant_id=r[2], engine=r[3], cell=r[4],
                metric=r[5], value=r[6], threshold=r[7],
                direction=Direction(r[8]) if r[8] else None, passed=r[9],
                run_kind=RunKind(r[10]) if r[10] else None, label_version=r[11],
                model_pins_hash=r[12], prompt_version=r[13], dataset_hash=r[14],
                git_sha=r[15], langfuse_trace_id=r[16], created_at=r[17],
            )
            for r in rows
        ]

    # ── kb_chunks (tenant content/voice partition — KNOW-02 voice grounding) ──

    def upsert_kb_chunk(
        self,
        *,
        tenant_id: str,
        content: str,
        kind: str = "post",
        metrics: dict[str, Any] | None = None,
        is_holdout: bool = False,
    ) -> str:
        """Insert (or refresh) one tenant content/voice chunk. Idempotent on the
        natural key (tenant, kind, content_hash) — a re-load never dups. ``content``
        is embedded for similarity retrieval. ``is_holdout`` tags a chunk that the
        rvy.4 brand-voice holdout scores against; such chunks are EXCLUDED from
        grounding reads (:meth:`voice_exemplars`) so the engine never grounds on the
        content it is graded on (voice-grounding-contract §1)."""
        if kind not in ("post", "voice"):
            raise ValueError(f"kb_chunks.kind {kind!r} not in ('post', 'voice')")
        chash = content_hash({"content": content})
        vec = self._embedder.embed(content)
        if len(vec) != 384:
            raise ValueError(f"embedding dim {len(vec)} != 384 (column is vector(384))")

        with self._conn(tenant_id) as conn:
            row = conn.execute(
                "INSERT INTO kb_chunks"
                " (tenant_id, kind, content, metrics, is_holdout, content_hash, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s::vector)"
                " ON CONFLICT (tenant_id, kind, content_hash)"
                " DO UPDATE SET metrics = EXCLUDED.metrics,"
                "   is_holdout = EXCLUDED.is_holdout, embedding = EXCLUDED.embedding"
                " RETURNING id",
                (
                    tenant_id, kind, content, json.dumps(metrics or {}),
                    is_holdout, chash, to_pgvector(vec),
                ),
            ).fetchone()
            return str(row[0])

    def voice_exemplars(
        self, *, tenant_id: str, query: str, k: int = 5
    ) -> list[Exemplar]:
        """Top-``k`` of the tenant's own past content nearest to ``query`` by cosine
        similarity — the KNOW-02 grounding retrieval the Copywriter consumes.

        Always tenant-scoped (explicit filter + RLS); holdout-tagged rows are excluded
        (§1 invariant). An empty / new-tenant KB returns ``[]`` (never raises), so the
        assembly degrades to dimensions-only. ``similarity`` is ``1 - cosine_distance``
        (higher = closer), per the contract."""
        qvec = to_pgvector(self._embedder.embed(query))
        with self._conn(tenant_id) as conn:
            rows = conn.execute(
                "SELECT content, metrics, 1 - (embedding <=> %s::vector) AS similarity"
                " FROM kb_chunks"
                " WHERE tenant_id = %s AND kind IN ('post', 'voice')"
                "   AND is_holdout = false AND embedding IS NOT NULL"
                " ORDER BY embedding <=> %s::vector ASC LIMIT %s",
                (qvec, tenant_id, qvec, k),
            ).fetchall()
        return [
            Exemplar(content=r[0], metrics=r[1] or {}, similarity=float(r[2]))
            for r in rows
        ]
