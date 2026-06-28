"""Tests for the Prometheus /metrics endpoint + recording API (CustomerAcq-13u).

DB-free: asserts the engine exposes the 3bu metric contract at /metrics in
Prometheus exposition format and that the recording API updates the series.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, generate_latest

import metrics
from main import app

# Every series the 3bu dashboard contract requires (infra/observability.md).
CONTRACT_METRICS = (
    "scalers_decisions_total",
    "scalers_queue_depth",
    "scalers_complaints_total",
    "scalers_actions_published_total",
    "scalers_publish_quota_used",
    "scalers_publish_quota_limit",
    "scalers_run_latency_seconds",
)


def _scrape() -> str:
    return generate_latest(REGISTRY).decode()


def test_metrics_endpoint_served_in_prometheus_format():
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    # Prometheus exposition has # HELP / # TYPE preamble lines.
    assert "# TYPE scalers_decisions_total counter" in resp.text


def test_metrics_path_does_not_redirect():
    # The 3bu scrape uses metrics_path: /metrics and does NOT follow redirects.
    # The exact path must return 200 directly — not a 307 to /metrics/.
    client = TestClient(app)
    resp = client.get("/metrics", follow_redirects=False)
    assert resp.status_code == 200, f"scrape path redirected: {resp.status_code}"


def test_all_contract_series_are_registered_and_exposed():
    body = _scrape()
    for name in CONTRACT_METRICS:
        # # TYPE line is emitted even before any labelled series exists.
        assert f"# TYPE {name} " in body, f"missing contract metric {name}"


def test_run_latency_is_a_histogram_with_quantile_buckets():
    body = _scrape()
    assert "# TYPE scalers_run_latency_seconds histogram" in body
    metrics.observe_run_latency(1.5, tenant="ink-studio")
    body = _scrape()
    # Histograms expose _bucket/_count/_sum so histogram_quantile works.
    assert 'scalers_run_latency_seconds_bucket{le="2.0",tenant="ink-studio"}' in body
    assert 'scalers_run_latency_seconds_count{tenant="ink-studio"}' in body


def test_record_decision_increments_labelled_counter():
    metrics.record_decision("auto", tenant="t-dec", channel="instagram")
    metrics.record_decision("auto", tenant="t-dec", channel="instagram")
    metrics.record_decision("review", tenant="t-dec", channel="gmail")
    value = REGISTRY.get_sample_value(
        "scalers_decisions_total",
        {"outcome": "auto", "tenant": "t-dec", "channel": "instagram"},
    )
    assert value == 2.0
    review = REGISTRY.get_sample_value(
        "scalers_decisions_total",
        {"outcome": "review", "tenant": "t-dec", "channel": "gmail"},
    )
    assert review == 1.0


def test_decision_outcomes_are_within_the_contract_set():
    assert set(metrics.DECISION_OUTCOMES) == {"auto", "review", "off"}


def test_gauge_and_counter_recording_api():
    metrics.set_publish_quota(tenant="t-q", channel="instagram", used=7, limit=25)
    assert REGISTRY.get_sample_value(
        "scalers_publish_quota_used", {"tenant": "t-q", "channel": "instagram"}
    ) == 7.0
    assert REGISTRY.get_sample_value(
        "scalers_publish_quota_limit", {"tenant": "t-q", "channel": "instagram"}
    ) == 25.0

    metrics.set_queue_depth(3, queue="publish", tenant="t-q")
    assert REGISTRY.get_sample_value(
        "scalers_queue_depth", {"queue": "publish", "tenant": "t-q"}
    ) == 3.0

    metrics.inc_published(tenant="t-q", channel="instagram")
    metrics.inc_complaint(tenant="t-q", channel="instagram")
    assert REGISTRY.get_sample_value(
        "scalers_actions_published_total", {"tenant": "t-q", "channel": "instagram"}
    ) == 1.0
    assert REGISTRY.get_sample_value(
        "scalers_complaints_total", {"tenant": "t-q", "channel": "instagram"}
    ) == 1.0


def test_gate_and_side_effect_recording():
    metrics.record_gate(tenant="t-g", gate="banned_phrase", passed=False)
    assert REGISTRY.get_sample_value(
        "scalers_gate_checks_total",
        {"tenant": "t-g", "gate": "banned_phrase", "result": "fail"},
    ) == 1.0
    metrics.record_side_effect(tenant="t-g", channel="posting", outcome="sent")
    assert REGISTRY.get_sample_value(
        "scalers_side_effects_total",
        {"tenant": "t-g", "channel": "posting", "outcome": "sent"},
    ) == 1.0


def test_cell_latency_recorded_by_a_real_cell_run():
    # A real typed-cell run records into scalers_cell_latency_seconds (base.py hook).
    from cells.content_brief import build_content_brief_cell
    from tests.conftest import VALID_BRIEF, tool_model

    cell = build_content_brief_cell()
    cell.run_sync("ctx", model=tool_model(VALID_BRIEF))
    count = REGISTRY.get_sample_value(
        "scalers_cell_latency_seconds_count", {"cell": "content_brief"}
    )
    assert count is not None and count >= 1.0
