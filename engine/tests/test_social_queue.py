"""Social Ready Queue — read model + the fail-closed Meta credential publish gate.

Three claims, each proven against the REAL paths (test_scheduler's DB patterns):

1. :func:`studio.social_queue.ready_posts` resolves a seeded pending IG action
   (context carrying a REAL artwork asset id) into a complete post package —
   caption, artwork tags + media kind, and the honest publish-gate state —
   DB-gated with a throwaway tenant, cleaned up.
2. ``approve_and_publish`` on an instagram action WITHOUT operator Meta
   credentials refuses FAIL-CLOSED: :class:`MetaCredentialsMissingError`, the
   row stays ``pending`` with the reason on ``last_error`` — never marked sent,
   never silently dropped.
3. The scheduler sweep (:func:`studio.scheduler.publish_due`) treats the
   credential refusal as BLOCKED: schedule cleared (no silent retry forever),
   reason recorded, draft still pending in the ready queue.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from studio.social_queue import _context_refs

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

_BLOCKED_IG = "Meta credentials not configured (META_PAGE_TOKEN / META_IG_USER_ID)"
_BLOCKED_FB = "Meta credentials not configured (META_PAGE_TOKEN / META_PAGE_ID)"


@pytest.fixture(autouse=True)
def _no_meta_creds(monkeypatch):
    # The gate under test: the operator has NOT provided Meta credentials yet.
    for key in ("META_PAGE_TOKEN", "META_IG_USER_ID", "META_PAGE_ID"):
        monkeypatch.delenv(key, raising=False)


# ── context reference parsing (pure, DB-free) ───────────────────────────────────


def test_context_refs_reads_enriched_json_context():
    ctx = json.dumps({
        "artist": "Maya",
        "artwork": {"assetId": "art_nested", "artifactId": "art_img_9"},
        "artwork_asset_id": "art_flat",
        "broll_asset_id": "vid_1",
    })
    # (artwork_asset_id, broll_asset_id, artwork_artifact_id) — the artifact id is
    # the render pointer the /studio/artifacts/{id}/raw route serves.
    assert _context_refs(ctx) == ("art_flat", "vid_1", "art_img_9")
    # Without the flat mirror, the nested artwork block still resolves both ids.
    nested_only = json.dumps({"artwork": {"assetId": "art_nested", "artifactId": "art_img_2"}})
    assert _context_refs(nested_only) == ("art_nested", None, "art_img_2")


def test_context_refs_reads_legacy_text_note_and_degrades_honestly():
    legacy = "Artwork: seed://maya/peony.png (asset art_abc12). Why: tagged floral."
    assert _context_refs(legacy) == ("art_abc12", None, None)
    # A JSON context that preserved the legacy note under 'note' also resolves.
    assert _context_refs(json.dumps({"note": legacy})) == ("art_abc12", None, None)
    # Absent / reference-free context resolves nothing — never an invented id.
    assert _context_refs(None) == (None, None, None)
    assert _context_refs("No artwork on file for this artist yet.") == (None, None, None)


def test_media_package_carries_the_image_url_for_a_real_image():
    from studio.social_queue import _media_package

    assets = {"art_1": {"styles": ["fine-line"], "motifs": ["peony"], "media": "image"}}
    pkg = _media_package("art_1", assets, None, artifact_id="art_img_1")
    assert pkg["found"] is True
    assert pkg["media"] == "image"
    assert pkg["image_url"] == "/studio/artifacts/art_img_1/raw"
    assert pkg["tags"] == ["fine-line", "peony"]
    # A missing artifact id yields no url, never a broken one.
    assert _media_package("art_1", assets, None)["image_url"] is None


def test_post_anatomy_leads_with_the_hook_and_surfaces_angle_cta_keywords():
    from studio.social_queue import _post_anatomy

    caption = "Which stem is yours?\n\nBook a full-day session.\n\nReply KEEBS."
    ctx = {"angle": "made_for_you", "cta": "dm to start", "hashtags": ["botanical", "fineline"]}
    a = _post_anatomy(ctx, caption)
    assert a["hook"] == "Which stem is yours?"
    assert a["angle"] == "made_for_you"
    assert a["cta"] == "dm to start"
    assert a["hashtags"] == ["botanical", "fineline"]
    # Honest-empty when the draft carried no structured fields.
    empty = _post_anatomy({}, "")
    assert empty == {"hook": None, "angle": None, "cta": None, "hashtags": []}


# ── (1) ready_posts resolves a seeded pending IG action into a package ──────────


@pytest.mark.integration
@_pg
def test_ready_posts_resolves_pending_ig_action_into_full_package(monkeypatch):
    import psycopg

    from studio.social_queue import ready_posts
    from team.store import TeamStore

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_socialq_" + uuid.uuid4().hex[:8]
    action_id = "act_sq_" + uuid.uuid4().hex[:8]
    asset_id = "art_sq_" + uuid.uuid4().hex[:8]

    # Honest-empty before anything is seeded for this throwaway tenant.
    assert ready_posts(tenant, dsn=dsn) == []

    TeamStore(dsn).setup()  # idempotent — ensures the assets table exists
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO assets (id, campaign_id, asset_type, content, status) "
            "VALUES (%s, %s, 'studio_artwork', %s, 'library')",
            (asset_id, f"portfolio:{tenant}", json.dumps({
                "artist": "Maya",
                "caption": "Fine-line peony on the forearm",
                "styles": ["fine-line", "floral"],
                "motifs": ["peony"],
            })),
        )
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status, context) "
            "VALUES (%s, %s, 'post', 'instagram', %s, 'pending', %s)",
            (action_id, tenant, "your story, made for you.\n\n#finelinetattoo",
             json.dumps({"note": "picked for tags", "artwork_asset_id": asset_id})),
        )
    try:
        posts = ready_posts(tenant, dsn=dsn)
        assert [p["action_id"] for p in posts] == [action_id]
        pkg = posts[0]
        assert pkg["channel"] == "instagram" and pkg["type"] == "post"
        assert pkg["caption"].startswith("your story")
        assert pkg["scheduled_for"] is None and pkg["schedule_live"] is False
        # Media resolved from the REAL assets row the context references.
        art = pkg["artwork"]
        assert art["asset_id"] == asset_id and art["found"] is True
        assert art["media"] == "image"
        assert art["tags"] == ["fine-line", "floral", "peony"]
        assert art["caption"] == "Fine-line peony on the forearm"
        assert pkg["broll"] is None  # none referenced — honest, not invented
        # The publish gate state, verbatim from the gate's own helper.
        assert pkg["publishable"] is False
        assert pkg["blocked_reason"] == _BLOCKED_IG

        # With the operator's credentials present the same package is publishable.
        monkeypatch.setenv("META_PAGE_TOKEN", "tok_it")
        monkeypatch.setenv("META_IG_USER_ID", "17840000000000000")
        armed = ready_posts(tenant, dsn=dsn)[0]
        assert armed["publishable"] is True and armed["blocked_reason"] is None
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE id = %s", (action_id,))
            conn.execute("DELETE FROM assets WHERE id = %s", (asset_id,))


@pytest.mark.integration
@_pg
def test_ready_posts_lists_fb_rows_with_the_facebook_gate(monkeypatch):
    """A pending 'fb' draft (the campaign spine's Channel.FB value) lists in the
    social ready queue as channel 'facebook' with the SAME honest publishable/
    blocked_reason fields the facebook gate enforces — and arms once the operator
    sets META_PAGE_TOKEN + META_PAGE_ID."""
    import psycopg

    from studio.social_queue import ready_posts

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_socialq_" + uuid.uuid4().hex[:8]
    action_id = "act_sq_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status) "
            "VALUES (%s, %s, 'post', 'fb', 'page-post caption', 'pending')",
            (action_id, tenant),
        )
    try:
        posts = ready_posts(tenant, dsn=dsn)
        assert [p["action_id"] for p in posts] == [action_id]
        pkg = posts[0]
        assert pkg["channel"] == "facebook"  # 'fb' alias folded, same as the gate
        assert pkg["caption"] == "page-post caption"
        assert pkg["publishable"] is False
        assert pkg["blocked_reason"] == _BLOCKED_FB

        # With the operator's facebook credentials present the row is publishable.
        monkeypatch.setenv("META_PAGE_TOKEN", "tok_it")
        monkeypatch.setenv("META_PAGE_ID", "17890000000")
        armed = ready_posts(tenant, dsn=dsn)[0]
        assert armed["publishable"] is True and armed["blocked_reason"] is None
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE id = %s", (action_id,))


# ── (2) approve without credentials refuses fail-closed ─────────────────────────


@pytest.mark.integration
@_pg
def test_approve_without_meta_credentials_stays_pending_with_reason(monkeypatch):
    import psycopg

    from actions.publish import MetaCredentialsMissingError, approve_and_publish
    from actions.store import get_action

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_socialq_" + uuid.uuid4().hex[:8]
    # The throwaway tenant has no registry row; passthrough the TEST-MODE gate so
    # the credential gate itself is what's under test (same pattern as the other
    # publish suites).
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", tenant)
    action_id = "act_sq_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status) "
            "VALUES (%s, %s, 'post', 'instagram', 'caption', 'pending')",
            (action_id, tenant),
        )
    try:
        with pytest.raises(MetaCredentialsMissingError) as ei:
            approve_and_publish(action_id, dsn=dsn)
        assert str(ei.value) == _BLOCKED_IG
        row = get_action(action_id, dsn=dsn)
        assert row.status == "pending"  # never claimed, never sent, never failed-silent
        assert row.last_error == _BLOCKED_IG
        assert row.sent_at is None and row.approved_at is None
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE id = %s", (action_id,))


# ── (3) the scheduler treats the credential refusal as blocked ──────────────────


@pytest.mark.integration
@_pg
def test_scheduler_publish_due_blocks_credentialless_ig_and_clears_schedule(monkeypatch):
    import psycopg

    import studio.scheduler as sched
    from actions.store import get_action

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_socialq_" + uuid.uuid4().hex[:8]
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", tenant)
    action_id = "act_sq_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status, "
            "scheduled_for) "
            "VALUES (%s, %s, 'post', 'instagram', 'caption', 'pending', "
            "now() - interval '1 minute')",
            (action_id, tenant),
        )
    # Scope the sweep to OUR row only (the shared DB may hold other due drafts);
    # the publish itself runs the REAL approve_and_publish + credential gate.
    monkeypatch.setattr(sched, "due_actions", lambda dsn=None, limit=10: [action_id])
    try:
        swept = sched.publish_due(dsn=dsn)
        blocked = [b for b in swept["blocked"] if b["actionId"] == action_id]
        assert blocked, f"expected a blocked entry, got {swept}"
        assert blocked[0]["reason"] == _BLOCKED_IG
        assert swept["failed"] == [] and swept["published"] == []
        row = get_action(action_id, dsn=dsn)
        assert row.status == "pending"          # still waiting in the ready queue
        assert row.scheduled_for is None        # schedule cleared — no silent retry
        assert row.last_error == _BLOCKED_IG    # reason recorded on the row
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE id = %s", (action_id,))


# ── (4) comment replies exempt from the META gate + tenant-scoped media ─────────


@pytest.mark.integration
@_pg
def test_ready_posts_comment_rows_exempt_and_foreign_assets_do_not_resolve(monkeypatch):
    """(a) A pending IG COMMENT reply carries NO META blocked_reason (replies
    publish via the engagement connector's own keys — see actions.publish); (b) an
    asset id from ANOTHER tenant's library reports found:false instead of leaking
    that tenant's media into this operator's approval package."""
    import psycopg

    from studio.social_queue import ready_posts
    from team.store import TeamStore

    for key in ("META_PAGE_TOKEN", "META_IG_USER_ID", "META_PAGE_ID"):
        monkeypatch.delenv(key, raising=False)
    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_socialq_" + uuid.uuid4().hex[:8]
    other_tenant = "t_socialq_" + uuid.uuid4().hex[:8]
    reply_id = "act_sq_" + uuid.uuid4().hex[:8]
    post_id = "act_sq_" + uuid.uuid4().hex[:8]
    foreign_asset = "art_sq_" + uuid.uuid4().hex[:8]

    TeamStore(dsn).setup()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO assets (id, campaign_id, asset_type, content, status) "
            "VALUES (%s, %s, 'studio_artwork', %s, 'library')",
            (foreign_asset, f"portfolio:{other_tenant}",
             json.dumps({"artist": "Other", "styles": ["bold"], "media": "video"})),
        )
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status) "
            "VALUES (%s, %s, 'comment', 'instagram', 'thanks, DM us!', 'pending')",
            (reply_id, tenant),
        )
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status, context) "
            "VALUES (%s, %s, 'post', 'instagram', 'new drop', 'pending', %s)",
            (post_id, tenant, json.dumps({"artwork_asset_id": foreign_asset})),
        )
    try:
        posts = {p["action_id"]: p for p in ready_posts(tenant, dsn=dsn)}
        reply = posts[reply_id]
        assert reply["type"] == "comment"
        assert reply["blocked_reason"] is None  # not gated on META_* keys
        post = posts[post_id]
        assert post["blocked_reason"] is not None  # posts stay gated
        # Foreign-tenant asset must not resolve into this tenant's package.
        assert post["artwork"]["found"] is False
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE id IN (%s, %s)", (reply_id, post_id))
            conn.execute("DELETE FROM assets WHERE id = %s", (foreign_asset,))


@pytest.mark.integration
@_pg
def test_load_broll_is_tenant_scoped():
    """A no-artist run must never pick up another tenant's video as b-roll: the
    library query is scoped to campaign_id='portfolio:<tenant>'."""
    import psycopg

    from studio.ig_pipeline import load_broll
    from team.store import TeamStore

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant_a = "t_broll_" + uuid.uuid4().hex[:8]
    tenant_b = "t_broll_" + uuid.uuid4().hex[:8]
    vid = "art_broll_" + uuid.uuid4().hex[:8]

    TeamStore(dsn).setup()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO assets (id, campaign_id, asset_type, content, status) "
            "VALUES (%s, %s, 'studio_artwork', %s, 'library')",
            (vid, f"portfolio:{tenant_a}",
             json.dumps({"artist": "Keebs", "media": "video", "caption": "reel"})),
        )
    try:
        mine = load_broll(tenant_a, None, dsn=dsn)
        assert [b["asset_id"] for b in mine] == [vid]
        # The other tenant sees NOTHING — not tenant A's newest video.
        assert load_broll(tenant_b, None, dsn=dsn) == []
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM assets WHERE id = %s", (vid,))
