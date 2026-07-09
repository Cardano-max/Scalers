"""Persistent agent-memory layer for the Campaign Studio.

A thin, first-party store over the EXISTING ``memories`` table (live DB, pgvector
``vector(384)`` column). Mirrors :mod:`kb.store` (real bge-small embedder, cosine
retrieval, tenant-scoped, idempotent upsert on a content-hash natural key). No new
datastore, no third-party memory framework — see
``docs/adr`` notes / the framework recommendation. Memories are INTERNAL context
only; they are never an action and never carry a send.
"""

from memory.store import Memory, MemoryStore

__all__ = ["Memory", "MemoryStore"]
