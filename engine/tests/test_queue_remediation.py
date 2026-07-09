"""wwy.7 queue-remediation: quarantine ALREADY-STAGED foreign-identity fabrications.

The generation-time guards stop new poison; this sweep cleans the rows staged BEFORE
the fix (the live "it's Rae from Ladies First" drafts against real skindesign
recipients). Hermetic: the actions store is faked so the LOGIC is exercised offline —
dry-run writes nothing, apply rejects only FOREIGN identity, own/honest copy untouched,
tenant-scoped, idempotent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import studio.queue_remediation as qr


def _row(id, *, tenant="skindesign", status="pending", subject="", draft="",
         type="outreach", channel="gmail", target="lead@example.com", is_seeded=False):
    return SimpleNamespace(id=id, tenant_id=tenant, status=status, subject=subject,
                           draft=draft, type=type, channel=channel, target=target,
                           is_seeded=is_seeded)


class _FakeStore:
    """In-memory stand-in for actions.store used by the sweep."""
    def __init__(self, rows):
        self.rows = {r.id: r for r in rows}
        self.updates: list[tuple[str, str, dict]] = []

    def list_actions(self, tenant_id, status=None, dsn=None):
        return [r for r in self.rows.values()
                if r.tenant_id == tenant_id and (status is None or r.status == status)]

    def get_action(self, action_id, dsn=None):
        return self.rows.get(action_id)

    def update_status(self, action_id, status, *, dsn=None, **fields):
        self.updates.append((action_id, status, fields))
        self.rows[action_id].status = status
        return self.rows[action_id]


@pytest.fixture
def wired(monkeypatch):
    def _wire(rows, *, protected="", declared=None):
        store = _FakeStore(rows)
        monkeypatch.setattr(qr, "list_actions", store.list_actions)
        monkeypatch.setattr(qr, "get_action", store.get_action)
        monkeypatch.setattr(qr, "update_status", store.update_status)
        monkeypatch.setenv("PROTECTED_TENANT_IDS", protected)
        if declared is None:
            monkeypatch.delenv("STUDIO_TENANT_ID", raising=False)
        else:
            monkeypatch.setenv("STUDIO_TENANT_ID", declared)
        return store
    return _wire


# Fresh rows per call — module-level rows would be MUTATED across tests (status flips
# to 'rejected'), leaking state and masking real behavior.
def _gun():
    return _row("act_gun", subject="hey Carlos - it's Rae from Ladies First",
                draft="I'd love to work with you again.", target="carlos@icloud.com")


def _clean():
    return _row("act_clean", subject="Hello from the studio",
                draft="Wanted to reach out and say hello.", target="dana@example.com")


def test_dry_run_flags_but_writes_nothing(wired):
    store = wired([_gun(), _clean()])
    rep = qr.sweep_foreign_identity("skindesign", apply=False)
    assert rep.scanned == 2
    assert [f["id"] for f in rep.flagged] == ["act_gun"]
    assert rep.quarantined == []
    assert store.updates == []                      # nothing written on a dry run
    assert store.rows["act_gun"].status == "pending"


def test_apply_quarantines_only_foreign_identity(wired):
    store = wired([_gun(), _clean()])
    rep = qr.sweep_foreign_identity("skindesign", apply=True)
    assert [q["id"] for q in rep.quarantined] == ["act_gun"]
    assert store.rows["act_gun"].status == "rejected"
    assert store.rows["act_clean"].status == "pending"   # honest copy untouched
    # the reason is recorded honestly in last_error
    (_id, _st, fields), = store.updates
    assert "foreign-identity" in fields["last_error"]
    assert "Ladies First" in fields["last_error"]


def test_own_identity_is_never_quarantined(wired):
    # ladies8391 legitimately naming ITSELF must not be swept.
    own = _row("act_own", tenant="ladies8391",
               subject="it's Rae from Ladies First", draft="come see us")
    wired([own])
    rep = qr.sweep_foreign_identity("ladies8391", apply=True)
    assert rep.flagged == [] and rep.quarantined == []


def test_failed_fabrication_is_also_quarantined(wired):
    failed = _row("act_failed", status="failed",
                  subject="it's Rae from Ladies First", draft="again")
    store = wired([failed])
    rep = qr.sweep_foreign_identity("skindesign", apply=True)
    assert [q["id"] for q in rep.quarantined] == ["act_failed"]
    assert store.rows["act_failed"].status == "rejected"


def test_idempotent_second_sweep_is_a_noop(wired):
    store = wired([_gun()])
    qr.sweep_foreign_identity("skindesign", apply=True)
    first = len(store.updates)
    # A rejected row is no longer in pending/failed, so a re-sweep flags nothing.
    rep2 = qr.sweep_foreign_identity("skindesign", apply=True)
    assert rep2.flagged == [] and rep2.quarantined == []
    assert len(store.updates) == first


def test_tenant_scope_blocks_protected_write_without_declaration(wired):
    wired([_gun()], protected="skindesign", declared=None)
    with pytest.raises(qr.assert_tenant_writable.__globals__["TenantWriteBlocked"]):
        qr.sweep_foreign_identity("skindesign", apply=True)


def test_protected_write_allowed_with_matching_declaration(wired):
    store = wired([_gun()], protected="skindesign", declared="skindesign")
    rep = qr.sweep_foreign_identity("skindesign", apply=True)
    assert [q["id"] for q in rep.quarantined] == ["act_gun"]
    assert store.rows["act_gun"].status == "rejected"


def test_skipped_when_status_moved_under_us(wired, monkeypatch):
    # A row claimed for send between scan and write is skipped, not clobbered.
    store = wired([_gun()])
    real_get = store.get_action

    def _moved(action_id, dsn=None):
        r = real_get(action_id, dsn=dsn)
        r.status = "sending"   # claimed concurrently
        return r
    monkeypatch.setattr(qr, "get_action", _moved)
    rep = qr.sweep_foreign_identity("skindesign", apply=True)
    assert rep.quarantined == []
    assert [s["id"] for s in rep.skipped] == ["act_gun"]
    assert store.updates == []
