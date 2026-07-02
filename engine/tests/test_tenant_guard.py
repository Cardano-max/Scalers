"""OPS-3 tenant write-guard (CustomerAcq-fr1.3, AC-4) — DB-free semantics.

The audit's motivating incident: a probe wrote a junk row into the CLIENT
tenant during the audit. The guard makes that impossible — a dev/prod process
can only write the tenant it explicitly declares via ``STUDIO_TENANT_ID``, and
a protected client tenant is unwritable unless that declaration matches it.
"""

from __future__ import annotations

import pytest

from ops.tenant_guard import TenantWriteBlocked, assert_tenant_writable


def test_no_env_no_protected_is_permissive(monkeypatch):
    # Plain dev default: nothing configured -> writes to a dev/test tenant pass.
    monkeypatch.delenv("STUDIO_TENANT_ID", raising=False)
    monkeypatch.delenv("PROTECTED_TENANT_IDS", raising=False)
    assert_tenant_writable("dev-tenant-123")  # no raise


def test_studio_tenant_id_mismatch_blocks(monkeypatch):
    # The process declared it operates tenant A; a write to tenant B is a bug
    # (the audit probe pointed at the wrong tenant) -> blocked.
    monkeypatch.setenv("STUDIO_TENANT_ID", "sdt-testmode")
    monkeypatch.delenv("PROTECTED_TENANT_IDS", raising=False)
    with pytest.raises(TenantWriteBlocked):
        assert_tenant_writable("skindesign-prod")


def test_studio_tenant_id_match_allows(monkeypatch):
    monkeypatch.setenv("STUDIO_TENANT_ID", "sdt-testmode")
    assert_tenant_writable("sdt-testmode")  # no raise


def test_protected_tenant_blocked_without_matching_declaration(monkeypatch):
    # A real client tenant is protected: writable ONLY by a process that
    # explicitly declares STUDIO_TENANT_ID == that tenant. Unset -> blocked.
    monkeypatch.delenv("STUDIO_TENANT_ID", raising=False)
    monkeypatch.setenv("PROTECTED_TENANT_IDS", "skindesign-prod,ladies8391")
    with pytest.raises(TenantWriteBlocked):
        assert_tenant_writable("skindesign-prod")


def test_protected_tenant_writable_with_exact_declaration(monkeypatch):
    monkeypatch.setenv("STUDIO_TENANT_ID", "skindesign-prod")
    monkeypatch.setenv("PROTECTED_TENANT_IDS", "skindesign-prod,ladies8391")
    assert_tenant_writable("skindesign-prod")  # explicit operator of that tenant


def test_dev_tenant_writable_even_with_protected_set(monkeypatch):
    # SDT onboards into a NON-protected test-mode tenant, so dev work is
    # unblocked while the real client tenants stay guarded.
    monkeypatch.delenv("STUDIO_TENANT_ID", raising=False)
    monkeypatch.setenv("PROTECTED_TENANT_IDS", "skindesign-prod")
    assert_tenant_writable("sdt-testmode")  # no raise


def test_block_message_names_the_tenant(monkeypatch):
    monkeypatch.setenv("STUDIO_TENANT_ID", "sdt-testmode")
    with pytest.raises(TenantWriteBlocked) as exc:
        assert_tenant_writable("skindesign-prod")
    assert "skindesign-prod" in str(exc.value)


def test_protected_ids_whitespace_tolerant(monkeypatch):
    monkeypatch.delenv("STUDIO_TENANT_ID", raising=False)
    monkeypatch.setenv("PROTECTED_TENANT_IDS", " skindesign-prod ,  ladies8391 ")
    with pytest.raises(TenantWriteBlocked):
        assert_tenant_writable("ladies8391")
