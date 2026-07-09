"""Campaign-level SAFE send (studio.campaign_send).

Two things are proven here:

  1. The eligibility filter is FAIL-CLOSED — only a draft with a computed confidence
     at/above threshold and no safety/gate escalation (and a valid recipient) is
     "eligible". A ``conf=None`` approval-required draft, a below-bar draft, a
     safety/gate-flagged draft, and an invalid-recipient draft are all NOT eligible.

  2. ``send_eligible`` / ``override_send`` route the real send through the EXISTING
     :func:`actions.publish.approve_and_publish` — exercising its atomic exactly-once
     claim AND its gmail allow-list/redirect — NOT a new bulk-send bypass. This is
     proven DB-free by faking the store UNDER the real ``approve_and_publish`` (the
     same seam ``test_gmail_safe_send`` uses), so the actual publish code runs.
"""

from __future__ import annotations

import datetime as _dt

import pytest

import actions.publish as publish
import actions.store as store_mod
import studio.campaign_send as cs
from actions.store import ActionRow
from connectors.gmail import GmailSendResult


@pytest.fixture(autouse=True)
def _legacy_passthrough_env(monkeypatch):
    # wwy.4: declare the legacy 'ladies8391' tenant as passthrough (production
    # sets TEST_MODE_LEGACY_PASSTHROUGH) so the fail-closed registry gate does
    # not refuse the campaign-send behavior under test.
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", "ladies8391,test_safe_send")


# ── eligibility filter (pure, always runs) ──────────────────────────────────────
def _row(**over) -> ActionRow:
    base = dict(
        id="act_x", tenant_id="ladies8391", type="post", channel="instagram",
        draft="caption", status="pending", target=None, conf=0.9, threshold=0.85,
        esc_kind="hold", worker="team",
    )
    base.update(over)
    return ActionRow(**base)


def test_eligible_when_confidence_clears_bar_and_no_safety_escalation():
    ok, _ = cs.eligibility(_row(conf=0.9, threshold=0.85, esc_kind="hold"))
    assert ok is True


def test_not_eligible_below_bar():
    ok, reason = cs.eligibility(_row(conf=0.5, threshold=0.85))
    assert ok is False and "below" in reason


def test_not_eligible_when_confidence_uncomputed():
    # The per-lead outreach drafts (approval_required, conf=None) are fail-closed.
    ok, reason = cs.eligibility(_row(conf=None, threshold=None, esc_kind="approval_required"))
    assert ok is False and "confidence" in reason


@pytest.mark.parametrize("esc", ["safety", "gate", "split", "media", "confidence"])
def test_not_eligible_on_safety_or_gate_escalation(esc):
    ok, reason = cs.eligibility(_row(conf=0.99, threshold=0.85, esc_kind=esc))
    assert ok is False and esc in reason


def test_not_eligible_when_not_pending():
    ok, _ = cs.eligibility(_row(status="sent"))
    assert ok is False


def test_gmail_recipient_must_be_valid_email():
    ok_bad, reason = cs.eligibility(
        _row(channel="gmail", target="not-an-email", conf=0.9, threshold=0.85)
    )
    assert ok_bad is False and "recipient" in reason
    ok_good, _ = cs.eligibility(
        _row(channel="gmail", target="lead@example.com", conf=0.9, threshold=0.85)
    )
    assert ok_good is True


# ── send_eligible routes through the REAL approve_and_publish (DB-free) ──────────
class _FakeStore:
    """Holds rows in memory and satisfies the get/update/claim seam that the REAL
    ``approve_and_publish`` calls — so the real publish code runs with no Postgres."""

    def __init__(self, *rows: ActionRow) -> None:
        self.rows = {r.id: r for r in rows}

    def get_action(self, action_id, dsn=None):
        return self.rows.get(action_id)

    def update_status(self, action_id, status, *, dsn=None, **fields):
        row = self.rows[action_id]
        row.status = status
        for k, v in fields.items():
            setattr(row, k, v)
        return row

    def claim_for_send(self, action_id, *, dsn=None):
        row = self.rows.get(action_id)
        if row is None or row.status != "pending":
            return None  # exactly-once: already claimed/sent
        row.status = "sending"
        row.autonomy = "approved"
        row.approved_at = _dt.datetime.now(_dt.timezone.utc)
        return row

    def pending(self):
        return [r for r in self.rows.values() if r.status == "pending"]


class _FakeGmail:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send(self, to, subject, body, *, from_addr=None):
        self.calls.append((to, subject, body))
        return GmailSendResult(message_id="m_fake", deep_link="dl_fake")


def _wire(monkeypatch, store: _FakeStore):
    # The seam under the real approve_and_publish.
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)
    # Enumeration + audit (avoid Postgres); capture audit calls.
    monkeypatch.setattr(store_mod, "list_actions_for_run",
                        lambda run_id, status=None, dsn=None: store.pending())
    audits: list[dict] = []
    import actions.audit as audit_mod
    monkeypatch.setattr(audit_mod, "record_send_audit",
                        lambda **kw: audits.append(kw) or "aud_test")
    return audits


def test_send_eligible_sends_only_eligible_through_approve_path(monkeypatch):
    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    eligible = _row(id="act_ok", channel="gmail", target="good@lead.com",
                    conf=0.9, threshold=0.85, esc_kind="hold", worker="studio_real_send",
                    subject="Hi", draft="Body", run_id="team-camp_a-r1")
    below = _row(id="act_low", channel="gmail", target="lo@lead.com", conf=0.4,
                 threshold=0.85, run_id="team-camp_a-r1")
    approval = _row(id="act_appr", channel="gmail", target="ap@lead.com", conf=None,
                    threshold=None, esc_kind="approval_required", run_id="team-camp_a-r1")
    store = _FakeStore(eligible, below, approval)
    audits = _wire(monkeypatch, store)
    gmail = _FakeGmail()

    out = cs.send_eligible(run_id="team-camp_a-r1", connectors={"gmail": gmail}, operator="op@x")

    # ONLY the eligible draft was sent — through the real approve path.
    assert gmail.calls == [("good@lead.com", "Hi", "Body")]
    assert out["n_sent"] == 1 and out["n_skipped"] == 2
    assert {s["action_id"] for s in out["skipped"]} == {"act_low", "act_appr"}
    assert store.rows["act_ok"].status == "sent"
    assert store.rows["act_low"].status == "pending"  # untouched
    # An audit row was written for the send.
    assert any(a["kind"] == "send_eligible" and a["action_id"] == "act_ok" for a in audits)

    # Exactly-once: re-running sends NOTHING again (the claim sees non-pending).
    out2 = cs.send_eligible(run_id="team-camp_a-r1", connectors={"gmail": gmail})
    assert len(gmail.calls) == 1
    assert out2["n_sent"] == 0


def test_send_eligible_honors_gmail_allow_list_redirect(monkeypatch):
    """A non-``studio_real_send`` eligible gmail draft is REDIRECTED to the operator
    inbox (allow-list), exactly as approve_and_publish does per-draft — proving the
    batch path did not bypass the redirect."""
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "operator@inbox.test")
    team_draft = _row(id="act_team", channel="gmail", target="stranger@real.com",
                      conf=0.9, threshold=0.85, esc_kind="hold", worker="team",
                      subject="Promo", draft="Body", run_id="team-camp_b-r1")
    store = _FakeStore(team_draft)
    _wire(monkeypatch, store)
    gmail = _FakeGmail()

    cs.send_eligible(run_id="team-camp_b-r1", connectors={"gmail": gmail})

    # Redirected to the operator inbox; the real stranger is NOT contacted.
    assert gmail.calls == [("operator@inbox.test", "[TEST->stranger@real.com] Promo", "Body")]


def test_send_eligible_live_true_sends_clean_and_surfaces_mode(monkeypatch):
    """EXPLICIT operator live authorization flips an eligible (but redirect-default)
    draft to a CLEAN live send to the real recipient, and the entry/audit carry
    mode='live'. Eligibility alone never does this — only ``live=True``."""
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "operator@inbox.test")
    draft = _row(id="act_live", channel="gmail", target="lead@real.com",
                 conf=0.9, threshold=0.85, esc_kind="hold", worker="team",
                 subject="Promo", draft="Body", run_id="team-camp_d-r1")
    store = _FakeStore(draft)
    audits = _wire(monkeypatch, store)
    gmail = _FakeGmail()

    out = cs.send_eligible(run_id="team-camp_d-r1", connectors={"gmail": gmail}, live=True)

    # Clean send to the real recipient (no [TEST] redirect), because the operator
    # explicitly authorized live.
    assert gmail.calls == [("lead@real.com", "Promo", "Body")]
    assert out["n_sent"] == 1
    assert out["sent"][0]["mode"] == "live"
    assert store.rows["act_live"].worker == "studio_real_send"  # single set-site
    assert any(a.get("mode") == "live" for a in audits)


def test_send_eligible_default_surfaces_test_redirect_mode(monkeypatch):
    """Without live authorization, an eligible gmail draft still redirects and the
    surfaced mode is 'test_redirect' (so the UI badges it Test)."""
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "operator@inbox.test")
    draft = _row(id="act_red", channel="gmail", target="lead@real.com",
                 conf=0.9, threshold=0.85, esc_kind="hold", worker="team",
                 subject="Promo", draft="Body", run_id="team-camp_e-r1")
    store = _FakeStore(draft)
    _wire(monkeypatch, store)
    gmail = _FakeGmail()

    out = cs.send_eligible(run_id="team-camp_e-r1", connectors={"gmail": gmail})

    assert out["sent"][0]["mode"] == "test_redirect"
    assert store.rows["act_red"].worker != "studio_real_send"


def test_override_requires_reason():
    with pytest.raises(cs.OverrideRequiresReasonError):
        cs.override_send("act_any", reason="   ")


def test_override_audits_then_sends_through_approve_path(monkeypatch):
    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    # A below-bar draft that send_eligible would NEVER send.
    low = _row(id="act_over", channel="gmail", target="vip@lead.com", conf=0.4,
               threshold=0.85, esc_kind="confidence", worker="studio_real_send",
               subject="Hi", draft="Body", run_id="team-camp_c-r1")
    store = _FakeStore(low)
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)
    monkeypatch.setattr(store_mod, "get_action", store.get_action)
    audits: list[dict] = []
    import actions.audit as audit_mod
    monkeypatch.setattr(audit_mod, "record_send_audit",
                        lambda **kw: audits.append(kw) or "aud_test")
    gmail = _FakeGmail()

    out = cs.override_send("act_over", reason="operator manually reviewed; approved",
                           operator="op@x", connectors={"gmail": gmail})

    # The override was audited BEFORE the send, with the reason + the (non-)eligibility.
    # (The publish path additionally writes its own per-send kind='send' audit row —
    # the unified delivery trail — so filter to the override rows here.)
    overrides = [a for a in audits if a["kind"] == "override"]
    assert len(overrides) == 1
    assert audits[0] is overrides[0]  # the override row is written FIRST (pre-send)
    assert overrides[0]["reason"] == "operator manually reviewed; approved"
    assert overrides[0]["eligible"] is False
    # And it sent through the real approve path.
    assert gmail.calls == [("vip@lead.com", "Hi", "Body")]
    assert out["result"] == "sent" and out["was_eligible"] is False

# Whole module needs a live Postgres (ENGINE_DATABASE_URL): it runs in the CI
# integration lane (schema applied via initdb + bootstrap), not the DB-free unit lane.
pytestmark = pytest.mark.integration
