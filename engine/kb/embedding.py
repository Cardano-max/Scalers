"""Local embedding path for the eval KB (KNOW-01, ADR Decision 2).

Embeddings run on a LOCAL model — free and private, no API keys (stack-decision).
This module ships a deterministic, dependency-free embedder so the scaffolding +
the per-commit eval gate stay hermetic (no torch, no network, reproducible). A
real local model (sentence-transformers `all-MiniLM-L6-v2`, or Ollama
`nomic-embed-text`) plugs in behind the same :class:`Embedder` protocol for
KNOW-02 grounding — only the dimension must match :data:`EMBED_DIM` (the pgvector
column is ``vector(384)``; a mismatch fails loudly on write, never truncates).
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Protocol, runtime_checkable

# Must equal the gold_example.embedding column dimension (infra/initdb/03-eval-kb.sql).
EMBED_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]:
        """Return a unit-length vector of length ``dim`` for ``text``."""
        ...


class DeterministicEmbedder:
    """A reproducible, dependency-free local embedder.

    Expands a SHA-256 digest of the text into ``dim`` floats and normalizes to
    unit length (cosine-friendly). It is NOT semantic — it stands in for a real
    local model so the schema, indexing, and tenant isolation are exercised
    end-to-end without heavy deps; swap in a semantic model behind ``Embedder``.
    """

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
            for i in range(0, len(digest), 4):
                # uint32 -> [-1, 1)
                out.append(struct.unpack(">I", digest[i : i + 4])[0] / 2**31 - 1.0)
                if len(out) >= self.dim:
                    break
            counter += 1
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]


def to_pgvector(embedding: list[float]) -> str:
    """Format a vector as the pgvector text literal ``[f1,f2,...]`` (cast ``::vector``)."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"
