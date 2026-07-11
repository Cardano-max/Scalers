"""Meta verify probe — the two failure classes must be told apart.

A real operator hit this pre-demo: the token was VALID (proved raw against the
Graph API) but META_APP_SECRET belonged to a different app, so every signed call
failed 'Invalid appsecret_proof' and the probe read ``verified: false`` — which
points at the wrong thing. The probe must verify the token on its own and name
the app-secret mismatch explicitly, while ``publishReady`` stays False (the
publish connectors always sign)."""

from __future__ import annotations

import pytest

import studio.meta_status as ms


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setenv("META_PAGE_TOKEN", "tok_valid")
    monkeypatch.setenv("META_IG_USER_ID", "17841400000000001")
    monkeypatch.setenv("META_PAGE_ID", "137700000000001")
    monkeypatch.setenv("META_APP_SECRET", "wrong_secret")


def _graph_stub(behavior):
    """behavior(path, proofed) -> dict | raises RuntimeError."""
    def stub(path, token, app_secret, fields):
        return behavior(path, app_secret is not None)
    return stub


def test_valid_token_wrong_secret_reports_mismatch_not_bad_token(monkeypatch):
    def behavior(path, proofed):
        if proofed:
            raise RuntimeError("HTTP 400: Invalid appsecret_proof provided in the API argument")
        if path.startswith("17841"):
            return {"id": "17841400000000001", "username": "skindesigntattoos"}
        return {"id": "137700000000001", "name": "SKIN DESIGN TATTOO"}

    monkeypatch.setattr(ms, "_graph_get", _graph_stub(behavior))
    out = ms.meta_verify()
    assert out["instagram"]["verified"] is True
    assert out["instagram"]["detail"] == "@skindesigntattoos"
    assert "MISMATCH" in out["instagram"]["appsecretProof"]
    assert out["facebook"]["verified"] is True
    assert out["publishReady"] is False  # signed calls still fail → cannot publish


def test_correct_secret_is_publish_ready(monkeypatch):
    def behavior(path, proofed):
        if path.startswith("17841"):
            return {"id": "17841400000000001", "username": "skindesigntattoos"}
        return {"id": "137700000000001", "name": "SKIN DESIGN TATTOO"}

    monkeypatch.setattr(ms, "_graph_get", _graph_stub(behavior))
    out = ms.meta_verify()
    assert out["instagram"]["verified"] is True
    assert out["instagram"]["appsecretProof"] == "ok"
    assert out["publishReady"] is True


def test_truly_bad_token_still_reads_unverified(monkeypatch):
    def behavior(path, proofed):
        raise RuntimeError("HTTP 401: Error validating access token: session has expired")

    monkeypatch.setattr(ms, "_graph_get", _graph_stub(behavior))
    out = ms.meta_verify()
    assert out["instagram"]["verified"] is False
    assert "expired" in out["instagram"]["detail"]
    assert out["publishReady"] is False


def test_missing_secret_verifies_token_but_blocks_publish(monkeypatch):
    monkeypatch.delenv("META_APP_SECRET", raising=False)

    def behavior(path, proofed):
        assert proofed is False  # no secret → probe never signs
        if path.startswith("17841"):
            return {"id": "17841400000000001", "username": "skindesigntattoos"}
        return {"id": "137700000000001", "name": "SKIN DESIGN TATTOO"}

    monkeypatch.setattr(ms, "_graph_get", _graph_stub(behavior))
    out = ms.meta_verify()
    assert out["instagram"]["verified"] is True
    assert "MISSING" in out["instagram"]["appsecretProof"]
    assert out["publishReady"] is False
