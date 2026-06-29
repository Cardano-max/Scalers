"""P0 Slice-6: the REAL provider error on a FAILED send is exposed + mapped.

Two hermetic checks (no DB, no network):

1. ``lastError`` is a field on BOTH read types in the GraphQL contract
   (``Action`` and ``ActivityItem``) — strawberry auto-camel-cases ``last_error``
   → ``lastError``, matching ``web/lib/data/queries.ts``.
2. ``repo._build_action`` carries ``actions.last_error`` through VERBATIM (the
   real Meta/Graph body), and only on the failed row — never fabricated.
"""

from __future__ import annotations

import re

from obsapi import repo
from obsapi.schema import schema

# A real Meta/Graph error body, as the live IG connector returns it (see
# actions.last_error on tenant ladies8391). Asserting verbatim pass-through.
REAL_GRAPH_ERROR = (
    "ig create media container failed: HTTP 400 145\n"
    '{"error":{"message":"Any of the pages_read_engagement, pages_manage_metadata,'
    ' pages_read_user_content permission(s) must be granted",'
    '"type":"OAuthException","code":190,"error_subcode":145}}'
)


def _type_block(sdl: str, name: str) -> str:
    m = re.search(r"type %s \{(.*?)\n\}" % re.escape(name), sdl, re.S)
    assert m, f"{name} type missing from SDL"
    return m.group(1)


def test_last_error_is_a_graphql_field_on_both_read_types():
    sdl = schema.as_str()
    assert "lastError" in _type_block(sdl, "Action"), "Action.lastError not exposed"
    assert "lastError" in _type_block(
        sdl, "ActivityItem"
    ), "ActivityItem.lastError not exposed"


def _row(**over):
    base = {
        "id": "act_x",
        "tenant_id": "ladies8391",
        "type": "post",
        "channel": "instagram",
        "worker": "publisher",
        "target": "@ladies8391",
        "created_at": None,
        "subject": None,
        "context": None,
        "draft": "caption",
        "conf": 0.5,
        "threshold": 0.8,
        "esc_kind": None,
        "esc_label": None,
        "idempotency_key": "k",
        "status": "failed",
        "recommend": None,
        "is_seeded": False,
        "last_error": REAL_GRAPH_ERROR,
    }
    base.update(over)
    return base


def test_build_action_carries_last_error_verbatim_on_failed_row():
    # No decision_id on the row, so the conn is never touched (None is safe).
    action = repo._build_action(None, _row())
    assert action.status == "FAILED"
    assert action.last_error == REAL_GRAPH_ERROR  # verbatim, not reformatted


def test_build_action_last_error_is_none_when_not_failed():
    action = repo._build_action(None, _row(status="sent", last_error=None))
    assert action.status == "SENT"
    assert action.last_error is None
