"""Unit tests for the local embedding path + natural key (KNOW-01, no DB)."""

from __future__ import annotations

import math

from kb import EMBED_DIM, DeterministicEmbedder, content_hash
from kb.embedding import to_pgvector


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
