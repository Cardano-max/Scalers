"""Unit tests for the local embedding path + natural key (KNOW-01, no DB)."""

from __future__ import annotations

import math

import pytest

from kb import DeterministicEmbedder, EMBED_DIM, content_hash, make_embedder
from kb.embedding import DEFAULT_MODEL, to_pgvector


def _cos(a, b):
    # embedders return L2-normalized vectors, so cosine == dot product.
    return sum(x * y for x, y in zip(a, b))


def test_embedder_returns_unit_vector_of_fixed_dim():
    emb = DeterministicEmbedder()
    v = emb.embed("a tattoo studio spring promo")
    assert len(v) == EMBED_DIM == 384
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


def test_embedding_is_deterministic_and_content_sensitive():
    emb = DeterministicEmbedder()
    assert emb.embed("same text") == emb.embed("same text")
    assert emb.embed("one") != emb.embed("two")


def test_to_pgvector_format():
    assert to_pgvector([1.0, -0.5]).startswith("[") and to_pgvector([1.0, -0.5]).endswith("]")
    assert to_pgvector([1.0, 2.0, 3.0]) == "[1.0,2.0,3.0]"


def test_content_hash_is_canonical_and_stable():
    # Key order does not change the hash (canonical JSON), but content does.
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})


# ── config-selectable factory (default = REAL, stub is opt-in) ────────────────


def test_factory_selects_deterministic_stub_for_offline_aliases():
    for alias in ("deterministic", "hash", "stub", "OFFLINE"):
        assert isinstance(make_embedder(alias), DeterministicEmbedder)


def test_factory_rejects_unknown_selector():
    # A typo must error loudly, never silently downgrade to the non-semantic stub.
    with pytest.raises(ValueError):
        make_embedder("bgee")


def test_default_selector_is_the_real_model_not_the_stub(monkeypatch):
    # With no override, the factory builds the REAL bge model (or raises if it
    # cannot load) — it must NOT default to the SHA-256 stub.
    monkeypatch.delenv("SCALERS_EMBEDDER", raising=False)
    try:
        emb = make_embedder()
    except RuntimeError:
        pytest.skip("real embedder backend unavailable on this box (offline)")
    assert not isinstance(emb, DeterministicEmbedder)
    assert getattr(emb, "model_name", "") == DEFAULT_MODEL


# ── the headline property: the real embedder is SEMANTIC, the stub is not ─────


def test_real_embedder_is_semantic_and_stub_is_not():
    """related cosine >> unrelated cosine for the real model; the SHA-256 stub
    shows no such separation (it cannot, by construction)."""
    try:
        real = make_embedder("bge")
    except RuntimeError:
        pytest.skip("real embedder backend unavailable on this box (offline)")

    a = "How do I get more tattoo clients booked for my studio?"
    b = "Tips to attract new customers to a tattoo shop and fill the calendar."
    c = "The mitochondria is the powerhouse of the cell in cellular biology."

    va, vb, vc = real.embed(a), real.embed(b), real.embed(c)
    assert len(va) == EMBED_DIM == 384
    assert math.isclose(math.sqrt(sum(x * x for x in va)), 1.0, rel_tol=1e-5)

    related, unrelated = _cos(va, vb), _cos(va, vc)
    assert related > unrelated + 0.2, f"related={related:.3f} not >> unrelated={unrelated:.3f}"

    stub = DeterministicEmbedder()
    sa, sb, sc = stub.embed(a), stub.embed(b), stub.embed(c)
    # The hash stub has no semantic structure: related vs unrelated are both ~0.
    assert abs(_cos(sa, sb) - _cos(sa, sc)) < 0.2
