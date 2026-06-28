"""Smoke test — proves the engine imports and the test runner is wired.

This is the canary the CI/done-gate green light depends on: if the engine
package or its FastAPI surface fail to import, this fails fast, independent of
any feature behaviour.
"""

import observability


def test_engine_packages_import():
    # The control core + thin portal must import cleanly.
    import harness  # noqa: F401
    import main  # noqa: F401
    from harness import graph, router, state  # noqa: F401


def test_fastapi_app_is_exposed():
    import main

    assert main.app.title.startswith("Scalers Growth Engine")


def test_observability_is_noop_when_unconfigured(monkeypatch):
    # The Langfuse seam must degrade to a no-op so tests/CI need no live server.
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    observability.get_langfuse.cache_clear()
    assert observability.get_langfuse() is None


def test_addition_sanity():
    assert 1 + 1 == 2
