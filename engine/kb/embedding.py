"""Local embedding path for the eval KB (KNOW-01, ADR Decision 2).

Embeddings run on a LOCAL model — free and private, no API keys (stack-decision).
The DEFAULT/active embedder is a REAL semantic model, ``BAAI/bge-small-en-v1.5``
(384-dim), loaded through :class:`FastEmbedEmbedder` (ONNX via ``fastembed`` — no
torch). It plugs in behind the :class:`Embedder` protocol; the pgvector column is
``vector(384)`` so the real model is a DROP-IN (no migration). A dimension
mismatch fails loudly on write, never truncates.

:class:`DeterministicEmbedder` (SHA-256 -> 384 floats) is kept for OFFLINE /
hermetic test use — it is NOT semantic and must never stand in for the real model
on a grounding path. Selection is config-driven (:func:`make_embedder`, env var
``SCALERS_EMBEDDER``): ``bge`` (default, real) or ``deterministic`` (offline).
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from functools import lru_cache
from typing import Protocol, runtime_checkable

# Must equal the gold_example/kb_chunks/voice embedding column dimension
# (infra/initdb/03-eval-kb.sql, 06-kb-content.sql). bge-small-en-v1.5 is 384-dim,
# so it is a drop-in for the deterministic stub — the column stays vector(384).
EMBED_DIM = 384

# The real local model. bge-small-en-v1.5 is the lightest strong English encoder
# that fastembed ships as a quantized ONNX graph (no torch); 384-dim by design.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

# Config knob: which embedder the factory builds when none is passed explicitly.
#   "bge" / "fastembed" / "real"  -> real semantic model (default)
#   "deterministic" / "hash" / "stub" -> SHA-256 stub (offline/hermetic tests)
EMBEDDER_ENV_VAR = "SCALERS_EMBEDDER"
_REAL_ALIASES = {"bge", "fastembed", "real", "semantic", "sentence-transformers", "st"}
_STUB_ALIASES = {"deterministic", "hash", "stub", "offline", "sha256"}


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]:
        """Return a unit-length vector of length ``dim`` for ``text``."""
        ...


def _l2_normalize(vec: list[float]) -> list[float]:
    """Scale ``vec`` to unit L2 length (cosine-friendly; a zero vector is left as-is)."""
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class DeterministicEmbedder:
    """A reproducible, dependency-free local embedder — NOT semantic.

    Expands a SHA-256 digest of the text into ``dim`` floats and normalizes to
    unit length (cosine-friendly). It does NOT capture meaning; it stands in for
    the real model so the schema, indexing, and tenant isolation are exercised
    end-to-end without heavy deps in hermetic/offline runs. The real semantic
    model (:class:`FastEmbedEmbedder`) is the runtime default — see
    :func:`make_embedder`.
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
        return _l2_normalize(out)


@lru_cache(maxsize=4)
def _load_fastembed_model(model_name: str):
    """Load (and cache) the fastembed ONNX model once per process.

    Imported lazily so ``kb.embedding`` stays importable (and the deterministic
    path usable) on a box without fastembed installed. The first call downloads
    and caches the quantized ONNX weights from HuggingFace; subsequent calls and
    embedders reuse the in-memory model.
    """
    from fastembed import TextEmbedding  # lazy: heavy import, optional offline

    return TextEmbedding(model_name=model_name)


class FastEmbedEmbedder:
    """REAL semantic embedder: ``BAAI/bge-small-en-v1.5`` via fastembed (ONNX).

    Produces genuine 384-dim sentence embeddings — related texts land close in
    cosine space, unrelated texts far apart (a hash embedder cannot do this).
    Output is L2-normalized so ``1 - cosine_distance`` is a clean similarity and
    the pgvector ``<=>`` (cosine) operator behaves as the contract expects.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, dim: int = EMBED_DIM) -> None:
        self.model_name = model_name
        self.dim = dim
        # Eagerly resolve the model so a load failure surfaces HERE (honesty
        # gate) rather than silently degrading on the first embed call.
        self._model = _load_fastembed_model(model_name)

    def embed(self, text: str) -> list[float]:
        vec = next(iter(self._model.embed([text])))
        out = [float(x) for x in vec]
        if len(out) != self.dim:
            raise ValueError(
                f"{self.model_name} produced dim {len(out)} != {self.dim} "
                f"(pgvector column is vector({self.dim}))"
            )
        return _l2_normalize(out)


@lru_cache(maxsize=4)
def _load_sentence_transformer(model_name: str):
    from sentence_transformers import SentenceTransformer  # lazy fallback

    return SentenceTransformer(model_name)


class SentenceTransformerEmbedder:
    """Fallback REAL embedder: same model via sentence-transformers (torch).

    Only used if fastembed will not load on the box (heavier — pulls torch). Same
    384-dim bge-small-en-v1.5 output, L2-normalized, so it is interchangeable with
    :class:`FastEmbedEmbedder` behind the protocol.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dim: int = EMBED_DIM) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model = _load_sentence_transformer(model_name)

    def embed(self, text: str) -> list[float]:
        vec = self._model.encode(text, normalize_embeddings=True)
        out = [float(x) for x in vec]
        if len(out) != self.dim:
            raise ValueError(
                f"{self.model_name} produced dim {len(out)} != {self.dim} "
                f"(pgvector column is vector({self.dim}))"
            )
        return _l2_normalize(out)


def _make_real_embedder(model_name: str = DEFAULT_MODEL) -> Embedder:
    """Build the real semantic embedder: fastembed first, sentence-transformers
    as a fallback ONLY if fastembed cannot load.

    HONESTY GATE: if neither real backend loads, RAISE — never silently fall back
    to the SHA-256 stub and pass it off as semantic.
    """
    try:
        return FastEmbedEmbedder(model_name)
    except Exception as fastembed_err:  # noqa: BLE001 — try the heavier fallback
        try:
            return SentenceTransformerEmbedder(model_name)
        except Exception as st_err:  # noqa: BLE001
            raise RuntimeError(
                "No real semantic embedder could be loaded. fastembed failed "
                f"({type(fastembed_err).__name__}: {fastembed_err}); "
                f"sentence-transformers failed ({type(st_err).__name__}: {st_err}). "
                "Install fastembed (preferred) or sentence-transformers, or set "
                f"{EMBEDDER_ENV_VAR}=deterministic to use the OFFLINE non-semantic "
                "stub explicitly (never as a stand-in for real grounding)."
            ) from st_err


def make_embedder(name: str | None = None, *, model_name: str = DEFAULT_MODEL) -> Embedder:
    """Return the configured embedder (config-selectable; default = REAL).

    ``name`` (or ``$SCALERS_EMBEDDER`` when ``name`` is None) selects:
      * ``bge`` / ``fastembed`` / ``real`` (DEFAULT) -> real bge-small-en-v1.5
      * ``deterministic`` / ``hash`` / ``stub`` -> SHA-256 offline stub
    An unknown value raises so a typo never silently downgrades to the stub.
    """
    selected = (name if name is not None else os.environ.get(EMBEDDER_ENV_VAR, "bge")).strip().lower()
    if selected in _STUB_ALIASES:
        return DeterministicEmbedder()
    if selected in _REAL_ALIASES:
        return _make_real_embedder(model_name)
    raise ValueError(
        f"unknown {EMBEDDER_ENV_VAR}={selected!r}; expected one of "
        f"{sorted(_REAL_ALIASES | _STUB_ALIASES)}"
    )


def default_embedder() -> Embedder:
    """The process-default embedder used by the stores when none is injected.

    Real semantic model unless ``$SCALERS_EMBEDDER`` selects the offline stub.
    """
    return make_embedder()


def to_pgvector(embedding: list[float]) -> str:
    """Format a vector as the pgvector text literal ``[f1,f2,...]`` (cast ``::vector``)."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"
