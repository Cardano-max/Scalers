"""Prometheus metrics for the engine (CustomerAcq-13u).

Exposes the metric contract the 3bu observability stack scrapes
(``infra/observability.md``) at ``/metrics`` on the FastAPI portal. The series
**names and labels are a contract** — the "Scalers — Engine Overview" Grafana
dashboard queries them, so they must stay stable.

This module is a leaf: it depends only on ``prometheus_client`` (no engine
imports), so any layer can record without creating a cycle, and recording is
best-effort — it never blocks a run.

Contract series (dashboard-queried):

* ``scalers_decisions_total{outcome,tenant,channel}`` — autonomy % (auto/all)
* ``scalers_queue_depth{queue,tenant}``
* ``scalers_complaints_total{tenant,channel}`` / ``scalers_actions_published_total{tenant,channel}`` — complaint rate
* ``scalers_publish_quota_used{tenant,channel}`` / ``scalers_publish_quota_limit{tenant,channel}`` — quota %
* ``scalers_run_latency_seconds{tenant}`` (histogram) — p50/p95/p99

Plus engine instrumentation in the same namespace (run counts, cell latency,
gate pass/fail, side-effect outcomes).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    make_asgi_app,
)

# Latency buckets sized for LLM-bearing runs (sub-second to minutes) so the
# p50/p95/p99 quantiles the dashboard computes are meaningful.
_RUN_BUCKETS = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
_CELL_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)

# Decision outcomes the dashboard understands (autonomy %). The router's
# ``regenerate`` is a quality reject, not an autonomy outcome; callers map it to
# ``review`` (non-auto). ``off`` is a disabled channel.
DECISION_OUTCOMES = ("auto", "review", "off")

# ── Contract series (names/labels are stable — see infra/observability.md) ────

DECISIONS = Counter(
    "scalers_decisions_total",
    "Autonomy decisions by outcome.",
    ["outcome", "tenant", "channel"],
)
QUEUE_DEPTH = Gauge(
    "scalers_queue_depth",
    "Pending items in a queue.",
    ["queue", "tenant"],
)
COMPLAINTS = Counter(
    "scalers_complaints_total",
    "Complaints received (complaint-rate numerator).",
    ["tenant", "channel"],
)
ACTIONS_PUBLISHED = Counter(
    "scalers_actions_published_total",
    "Actions published (complaint-rate denominator).",
    ["tenant", "channel"],
)
PUBLISH_QUOTA_USED = Gauge(
    "scalers_publish_quota_used",
    "Publishes used in the rolling window.",
    ["tenant", "channel"],
)
PUBLISH_QUOTA_LIMIT = Gauge(
    "scalers_publish_quota_limit",
    "Publish quota limit (from IG content_publishing_limit; default 25/24h).",
    ["tenant", "channel"],
)
RUN_LATENCY = Histogram(
    "scalers_run_latency_seconds",
    "End-to-end run latency.",
    ["tenant"],
    buckets=_RUN_BUCKETS,
)

# ── Engine instrumentation (same namespace; not dashboard-contract) ───────────

RUNS = Counter(
    "scalers_runs_total",
    "Engine runs by terminal status.",
    ["tenant", "status"],
)
CELL_LATENCY = Histogram(
    "scalers_cell_latency_seconds",
    "Typed-cell run latency.",
    ["cell"],
    buckets=_CELL_BUCKETS,
)
GATE_CHECKS = Counter(
    "scalers_gate_checks_total",
    "Deterministic gate checks by result.",
    ["tenant", "gate", "result"],
)
SIDE_EFFECTS = Counter(
    "scalers_side_effects_total",
    "Side-effect dispatch outcomes.",
    ["tenant", "channel", "outcome"],
)


# ── Recording API (callers use these; never touch label internals directly) ──


def record_decision(outcome: str, *, tenant: str, channel: str) -> None:
    """Count one autonomy decision. ``outcome`` should be in ``DECISION_OUTCOMES``."""
    DECISIONS.labels(outcome=outcome, tenant=tenant, channel=channel).inc()


def record_run(*, tenant: str, status: str) -> None:
    RUNS.labels(tenant=tenant, status=status).inc()


def observe_run_latency(seconds: float, *, tenant: str) -> None:
    RUN_LATENCY.labels(tenant=tenant).observe(seconds)


@contextmanager
def time_run(*, tenant: str) -> Iterator[None]:
    """Context manager timing a run into ``scalers_run_latency_seconds``."""
    with RUN_LATENCY.labels(tenant=tenant).time():
        yield


def observe_cell_latency(seconds: float, *, cell: str) -> None:
    CELL_LATENCY.labels(cell=cell).observe(seconds)


@contextmanager
def time_cell(*, cell: str) -> Iterator[None]:
    """Context manager timing a cell run into ``scalers_cell_latency_seconds``."""
    with CELL_LATENCY.labels(cell=cell).time():
        yield


def record_gate(*, tenant: str, gate: str, passed: bool) -> None:
    GATE_CHECKS.labels(tenant=tenant, gate=gate, result="pass" if passed else "fail").inc()


def record_side_effect(*, tenant: str, channel: str, outcome: str) -> None:
    """Count a side-effect dispatch outcome (e.g. ``sent`` / ``failed`` / ``deduped``)."""
    SIDE_EFFECTS.labels(tenant=tenant, channel=channel, outcome=outcome).inc()


def inc_published(*, tenant: str, channel: str, n: int = 1) -> None:
    ACTIONS_PUBLISHED.labels(tenant=tenant, channel=channel).inc(n)


def inc_complaint(*, tenant: str, channel: str, n: int = 1) -> None:
    COMPLAINTS.labels(tenant=tenant, channel=channel).inc(n)


def set_queue_depth(value: float, *, queue: str, tenant: str) -> None:
    QUEUE_DEPTH.labels(queue=queue, tenant=tenant).set(value)


def set_publish_quota(*, tenant: str, channel: str, used: float, limit: float) -> None:
    PUBLISH_QUOTA_USED.labels(tenant=tenant, channel=channel).set(used)
    PUBLISH_QUOTA_LIMIT.labels(tenant=tenant, channel=channel).set(limit)


def render() -> tuple[bytes, str]:
    """Render the current exposition + its content type.

    Served at the EXACT path ``/metrics`` (no trailing-slash redirect) so the 3bu
    Prometheus scrape (``metrics_path: /metrics``) succeeds — Prometheus does not
    follow redirects on a scrape, so a mounted sub-app (which 307s ``/metrics`` ->
    ``/metrics/``) would leave the target DOWN.
    """
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def metrics_asgi_app():
    """ASGI app serving the Prometheus exposition (alternative to :func:`render`)."""
    return make_asgi_app()


__all__ = [
    "CONTENT_TYPE_LATEST",
    "DECISION_OUTCOMES",
    "render",
    "metrics_asgi_app",
    "record_decision",
    "record_run",
    "observe_run_latency",
    "time_run",
    "observe_cell_latency",
    "time_cell",
    "record_gate",
    "record_side_effect",
    "inc_published",
    "inc_complaint",
    "set_queue_depth",
    "set_publish_quota",
]
