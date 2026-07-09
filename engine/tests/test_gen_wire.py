"""SD-GEN-WIRE (CustomerAcq-ju1.7): the ju1.4 example-grounded generator is reachable from
the console supervisor, and its real grounded example-ids surface in the draft lineage.

Closes ju1.6 acceptance items 6 (generate in console) + 9 (grounding visible in lineage):
  * ``generate_example_campaign`` is a registered ``studio_agent`` tool (so the supervisor
    can invoke it from ``/studio/agui`` chat);
  * the ``/studio/action/{id}/lineage`` endpoint resolves a campaign_generator draft's
    ``context.grounded_example_ids`` to the REAL example names — honest-empty otherwise.
"""

from __future__ import annotations

import os

import pytest

_DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)


# ── item 6: the generator is reachable from the supervisor ────────────────────


def test_generate_example_campaign_is_a_registered_supervisor_tool():
    import studio.agui as agui

    tools = agui.studio_agent._function_toolset.tools
    assert "generate_example_campaign" in tools, sorted(tools.keys())


def test_tool_signature_takes_artist_and_optional_offer():
    import inspect

    import studio.agui as agui

    sig = inspect.signature(agui.generate_example_campaign)
    params = sig.parameters
    assert "artist" in params
    # offer inputs are OPTIONAL (offer discipline: only when the operator states them)
    for opt in ("offer_price_usd", "payment_plan", "spots"):
        assert params[opt].default is None, opt


# ── item 9: grounded example-ids resolve to real names in the lineage ─────────


def test_resolve_example_lineage_empty_for_no_ids():
    from studio.console_api import _resolve_example_lineage

    assert _resolve_example_lineage("skindesign", None) == []
    assert _resolve_example_lineage("skindesign", []) == []
    assert _resolve_example_lineage("skindesign", "not-a-list") == []


@pytest.mark.integration
def test_resolve_example_lineage_returns_real_names_in_order():
    import psycopg

    from studio.console_api import _resolve_example_lineage

    try:
        with psycopg.connect(_DSN, autocommit=True) as c:
            rows = c.execute(
                "SELECT id, campaign_name FROM campaign_examples "
                "WHERE tenant_id='skindesign' AND artist_name='Angel' ORDER BY follow_up_to NULLS FIRST"
            ).fetchall()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no Postgres: {exc}")
    if len(rows) < 2:
        pytest.skip("skindesign Angel examples not imported")

    opener_id, followup_id = rows[0][0], rows[1][0]
    # order preserved; unknown id dropped; never fabricated
    out = _resolve_example_lineage("skindesign", [followup_id, "cex_does_not_exist", opener_id])
    assert [e["id"] for e in out] == [followup_id, opener_id]
    assert all(e["campaign_name"] and e["artist"] == "Angel" for e in out)


@pytest.mark.integration
def test_action_lineage_surfaces_grounded_examples_for_generated_draft():
    import psycopg

    from studio.campaign_generator import generate_campaign, stage_campaign
    from studio.console_api import action_lineage

    try:
        with psycopg.connect(_DSN, autocommit=True) as c:
            n = c.execute("SELECT count(*) FROM campaign_examples WHERE tenant_id='skindesign' "
                          "AND artist_name='Angel'").fetchone()[0]
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no Postgres: {exc}")
    if n == 0:
        pytest.skip("skindesign Angel examples not imported")

    campaign = generate_campaign("skindesign", artist="Angel", offer_price_usd=1200,
                                 payment_plan="Klarna & Affirm", spots=5, dsn=_DSN)
    run_id = "ju17-lineage-test"
    try:
        staged = stage_campaign(campaign, run_id=run_id, dsn=_DSN)
        assert staged
        with psycopg.connect(_DSN, autocommit=True) as c:
            aid = c.execute("SELECT id FROM actions WHERE run_id=%s AND channel='email' LIMIT 1",
                            (run_id,)).fetchone()[0]
        lineage = action_lineage(aid)
        # The grounding is now VISIBLE in the lineage (item 9), citing a REAL example id.
        assert lineage["examples"], "lineage did not surface the grounded example"
        assert all(e["id"].startswith("cex_") for e in lineage["examples"])
        assert all(e["campaign_name"] and e["artist"] == "Angel" for e in lineage["examples"])
        assert {e["id"] for e in lineage["examples"]} <= set(campaign.grounded_example_ids)
        # The operator's offer surfaces too; artist is named.
        assert lineage["offer"] == "$1,200"
        assert lineage["artist"] == "Angel"
    finally:
        with psycopg.connect(_DSN, autocommit=True) as c:
            c.execute("DELETE FROM actions WHERE run_id=%s", (run_id,))
