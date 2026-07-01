"""Multimodal ingestion — dense docs / artwork into GROUNDED, CITED brand memory.

Turns a brand playbook, artist deck, portfolio, or artwork image into STRUCTURED
brand-/artist-persona facts the agent stack can ground on, where every extracted
fact carries a REAL citation back to its source locus (doc id + page/char span, or
image ref). This feeds brand-voice + artist-persona grounding alongside the
:mod:`studio.documents` doc store and the :mod:`studio.offers` substantiation gate.

NO-FABRICATION GATE — the load-bearing design choice
----------------------------------------------------
The grounding is the Anthropic **Citations** feature, not the model's self-report.
When we send a document with ``citations.enabled=True``, the API attaches a real
``cited_text`` span (a char/page/block location into the ACTUAL source bytes) to
each factual claim it draws from the document. A fact is emitted **only** when a
real citation span overlaps it; a claim the model states without a citation is
DROPPED and counted in ``dropped_uncited``. The model therefore cannot invent a
locus — the locus comes from the API's own indexing of the source, not a number
the model typed. This is the exact analogue of the offers substantiation gate
(:mod:`studio.offers`): cited-or-it-does-not-exist.

BELT-AND-SUSPENDERS on the text/markdown path (where we still hold the source we
sent), a SECOND gate re-verifies each cited span is LITERALLY present in the source
(normalized substring — the same evidence discipline as :mod:`studio.psych_profile`).
A cited fact whose span can't be found in the source is DROPPED and counted in
``dropped_unverified``, never kept. (PDF text lives server-side, so this gate is not
applied there; the API citation is the sole grounding for the PDF path.)

HONEST DEGRADATION
------------------
If neither a model key nor a supported parser is available this module raises
:class:`NotConfiguredError` and returns NO facts — it never fabricates extracted
content. Unconfigured surfaces (missing ``ANTHROPIC_API_KEY``, missing ``anthropic``
SDK) and unparseable formats (DOCX/PPTX/XLSX with no converter present) both fail
closed with a clear message.

What is natively supported
--------------------------
* **PDF** (base64 document block) — page-level citation locus (``p.N`` / ``pp.N-M``).
* **text / markdown** (text document block) — char-level citation locus (``chars A-B``).
* **images** (PNG/JPEG/WebP/GIF) — the VLM describes the artwork; the API does NOT
  emit span citations for images, so the locus is the whole image (``image:<id>``)
  and facts are marked ``signal="image_visual"`` (image-level, weaker than a span).
* **DOCX / PPTX / XLSX** — not natively decodable by the model API and no converter
  is bundled, so these raise :class:`NotConfiguredError` (convert to PDF/text first).
  Tabular XLSX/CSV data belongs in the CSV-aware path of :mod:`studio.documents`.

The pure extraction core (:func:`facts_from_blocks`) is separated from the network
call so it is unit-testable offline against synthetic Anthropic response blocks.
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Errors.
# --------------------------------------------------------------------------- #


class NotConfiguredError(RuntimeError):
    """No model key / SDK / parser is available, so ingestion cannot run.

    Raised at use time (never at import). Carries a clear, value-free message so
    the caller degrades honestly instead of receiving fabricated extraction. The
    contract is fail-closed: when this is raised, ZERO facts were produced.
    """


# --------------------------------------------------------------------------- #
# Controlled field vocabulary — the structured fields the agent stack grounds on.
# A bracketed tag outside this set is still accepted (kept verbatim, snake-cased)
# so we never silently discard a real fact; the set documents the intended shape.
# --------------------------------------------------------------------------- #
FIELD_TAGS: frozenset[str] = frozenset(
    {
        "brand_voice",
        "tone",
        "audience",
        "visual_style",
        "positioning",
        "value_prop",
        "differentiator",
        "offer",
        "service",
        "claim",
        "do",
        "dont",
        "artist_persona",
        "fact",
    }
)

# --------------------------------------------------------------------------- #
# Format detection.
# --------------------------------------------------------------------------- #
_EXT_MEDIA: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/plain",
    ".markdown": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Formats we recognise but cannot natively parse without a converter. Named so the
# degradation message is specific rather than a generic "unsupported".
_NEEDS_CONVERTER = {
    ".docx": "Word",
    ".doc": "Word",
    ".pptx": "PowerPoint",
    ".ppt": "PowerPoint",
    ".xlsx": "Excel",
    ".xls": "Excel",
}


def guess_media_type(name: str) -> str | None:
    """Media type from a filename extension, or ``None`` when unsupported.

    ``None`` covers both truly unknown extensions and the convert-first family
    (DOCX/PPTX/XLSX) — the caller turns that into a :class:`NotConfiguredError`."""
    return _EXT_MEDIA.get(Path(name or "").suffix.lower())


def _source_kind(media_type: str | None) -> str | None:
    """Map a media type to an extraction path: ``"pdf" | "text" | "image"`` or None."""
    if not media_type:
        return None
    if media_type == "application/pdf":
        return "pdf"
    if media_type.startswith("text/"):
        return "text"
    if media_type.startswith("image/"):
        return "image"
    return None


# --------------------------------------------------------------------------- #
# Records.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Citation:
    """A real citation back to the source: doc id + locus (never invented)."""

    source_doc_id: str
    locus: str  # "chars A-B" | "p.N" | "pp.N-M" | "block A-B" | "image:<id>"
    kind: str  # "char" | "page" | "content_block" | "image"
    cited_text: str = ""
    document_title: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_doc_id": self.source_doc_id,
            "locus": self.locus,
            "kind": self.kind,
            "cited_text": self.cited_text,
            "document_title": self.document_title,
        }


@dataclass(frozen=True)
class ExtractedFact:
    """One grounded fact: ``{field, value, citation, signal}`` — cited or it does
    not exist. ``signal`` is ``"cited"`` for a span-grounded fact (PDF/text) and
    ``"image_visual"`` for an image-level (whole-image) observation."""

    field: str
    value: str
    citation: Citation
    signal: str = "cited"

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "citation": self.citation.as_dict(),
            "signal": self.signal,
        }


@dataclass(frozen=True)
class IngestResult:
    """The structured, tenant-scoped result of one ingestion."""

    tenant_id: str
    source_doc_id: str
    source_kind: str
    model: str
    facts: list[ExtractedFact] = field(default_factory=list)
    dropped_uncited: int = 0
    dropped_unverified: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "source_doc_id": self.source_doc_id,
            "source_kind": self.source_kind,
            "model": self.model,
            "fact_count": len(self.facts),
            "dropped_uncited": self.dropped_uncited,
            "dropped_unverified": self.dropped_unverified,
            "facts": [f.as_dict() for f in self.facts],
        }


# --------------------------------------------------------------------------- #
# Prompt.
# --------------------------------------------------------------------------- #
_TAG_LIST = (
    "[brand_voice] [tone] [audience] [visual_style] [positioning] "
    "[value_prop] [differentiator] [offer] [service] [claim] [do] [dont] [artist_persona]"
)

_SYSTEM_PROMPT = (
    "You extract STRUCTURED brand and artist facts from an internal document for a "
    "tattoo studio's marketing agent. Rules, all mandatory:\n"
    "1. State ONLY facts DIRECTLY SUPPORTED by the document. Never infer, embellish, "
    "generalize, or add outside knowledge.\n"
    "2. Output ONE fact per line, each prefixed with a field tag in square brackets "
    f"from this set: {_TAG_LIST}. If none fit, use [fact].\n"
    "3. Keep each fact to one short, self-contained sentence.\n"
    "4. Cite the exact source passage for every fact.\n"
    "5. If the document contains no usable brand/artist facts, output the single "
    "line: NONE\n"
    "Output nothing except the tagged fact lines (or NONE) — no preamble, no summary."
)

_USER_INSTRUCTION = "Extract the brand and artist facts from the document above."
_IMAGE_INSTRUCTION = (
    "Extract brand and artist facts that are VISIBLY present in the image above "
    "(style, motifs, palette, technique). Describe only what is actually shown."
)


# --------------------------------------------------------------------------- #
# Configuration / client (network boundary — the only impure part).
# --------------------------------------------------------------------------- #
def is_configured() -> bool:
    """True iff a non-empty ``ANTHROPIC_API_KEY`` and the ``anthropic`` SDK are present."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not key.strip():
        return False
    try:
        import anthropic  # noqa: F401
    except Exception:
        return False
    return True


def _default_model() -> str:
    """Pinned ingestion model. ``SCALERS_INGEST_MODEL`` overrides; otherwise the
    engine's pinned Sonnet (harness.config.DEFAULT_SONNET) — same pinned-model
    discipline as the typed cells (HARN-06)."""
    env = os.environ.get("SCALERS_INGEST_MODEL")
    if env and env.strip():
        return env.strip()
    try:
        from harness.config import DEFAULT_SONNET

        return DEFAULT_SONNET
    except Exception:
        return "claude-sonnet-4-6"


def _client():
    """Build a real Anthropic client, or raise :class:`NotConfiguredError`.

    Fail-closed: an absent/empty key or a missing SDK raises here BEFORE any work,
    so an unconfigured environment degrades honestly instead of fabricating."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not key.strip():
        raise NotConfiguredError(
            "ANTHROPIC_API_KEY is not set; multimodal ingestion requires a "
            "configured Anthropic model key. No extraction is produced when "
            "unconfigured (honest degradation, never fabricated)."
        )
    try:
        import anthropic
    except Exception as exc:  # pragma: no cover - environment-dependent
        raise NotConfiguredError(
            "the 'anthropic' SDK is not installed; multimodal ingestion cannot run. "
            "No extraction is fabricated when the parser is unavailable."
        ) from exc
    return anthropic.Anthropic(api_key=key)


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O — unit-testable without a network or SDK).
# --------------------------------------------------------------------------- #
def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a pydantic block OR a plain dict (SDK returns objects;
    tests pass dict/SimpleNamespace)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


_TAG_RE = re.compile(r"^\s*[-*]?\s*\[\s*([A-Za-z][A-Za-z _]{1,30})\s*\]\s*(.*)$", re.S)
_NONE_MARKERS = {"NONE", "N/A", "NO FACTS", "NO FACTS FOUND"}


def _split_tag(line: str) -> tuple[str | None, str]:
    """Split a leading ``[tag]`` off a line → ``(normalized_tag_or_None, rest)``."""
    m = _TAG_RE.match(line or "")
    if not m:
        return None, (line or "")
    tag = m.group(1).strip().lower().replace(" ", "_")
    return tag, m.group(2)


def _is_none_marker(text: str) -> bool:
    return text.strip().upper() in _NONE_MARKERS


# Evidence-match normalization, identical to studio.psych_profile._norm: lowercase +
# collapse every non-alphanumeric run (punctuation/whitespace) to a single space, then
# strip. Applied to BOTH the source and the cited span so a real quote survives trivial
# formatting differences, but a quote that is genuinely absent still fails the substring
# check — always erring toward DROP over a fabricated fact.
_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _NORM_RE.sub(" ", (text or "").lower()).strip()


def _cited_in_source(cited_text: str, source_text: str) -> bool:
    """Belt-and-suspenders literal-match gate (same discipline as psych_profile's
    evidence check): the normalized cited span must be a substring of the normalized
    source. An empty cited span cannot be verified, so it fails closed."""
    needle = _norm(cited_text)
    if not needle:
        return False
    return needle in _norm(source_text)


def _citation_from(cit: Any, source_doc_id: str, document_title: str | None) -> Citation:
    """Build a :class:`Citation` from an Anthropic citation object/dict.

    The locus is derived from the API-supplied span (char/page/block) — it is the
    source's own coordinates, not a value the model chose."""
    ctype = _attr(cit, "type")
    cited = _attr(cit, "cited_text") or ""
    title = _attr(cit, "document_title") or document_title
    if ctype == "char_location":
        s, e = _attr(cit, "start_char_index"), _attr(cit, "end_char_index")
        return Citation(source_doc_id, f"chars {s}-{e}", "char", cited, title)
    if ctype == "page_location":
        s, e = _attr(cit, "start_page_number"), _attr(cit, "end_page_number")
        # Page numbers are 1-indexed; end is exclusive, so a single page N is [N, N+1).
        last = e - 1 if isinstance(s, int) and isinstance(e, int) and e > s else s
        locus = f"p.{s}" if last == s else f"pp.{s}-{last}"
        return Citation(source_doc_id, locus, "page", cited, title)
    if ctype == "content_block_location":
        s, e = _attr(cit, "start_block_index"), _attr(cit, "end_block_index")
        return Citation(source_doc_id, f"block {s}-{e}", "content_block", cited, title)
    # Unknown/other citation type — keep it, honestly labelled, never dropped silently.
    return Citation(source_doc_id, str(ctype), str(ctype or "unknown"), cited, title)


def facts_from_blocks(
    blocks: Any,
    *,
    source_doc_id: str,
    document_title: str | None = None,
    source_text: str | None = None,
) -> tuple[list[ExtractedFact], int, int]:
    """PURE core: turn Anthropic response content blocks into grounded facts.

    A fact is emitted for a line ONLY when a real citation span overlaps that line
    (cited-or-it-does-not-exist). Robust to how the API chunks text into cited and
    connective blocks: the text is reconstructed by concatenation, citations are
    mapped to their char span in that concatenation, and lines are matched against
    overlapping citation spans.

    When ``source_text`` is supplied (the text/markdown path — we hold the source we
    sent), a SECOND gate applies on top of the API grounding: the cited span must be
    literally present in the source (normalized substring, :func:`_cited_in_source`).
    A cited fact whose span can't be verified against the source is DROPPED, not kept
    — belt-and-suspenders over the API's own citation, matching the evidence discipline
    in :mod:`studio.psych_profile`.

    Returns ``(facts, dropped_uncited, dropped_unverified)``: ``dropped_uncited``
    counts substantive claim lines that had NO citation; ``dropped_unverified`` counts
    cited lines whose span failed the literal-match gate."""
    # 1. Reconstruct the full text and remember each text segment's char span +
    #    its citations, so a citation can be located within the concatenated text.
    full = ""
    seg_spans: list[tuple[int, int, list[Any]]] = []
    for b in blocks or []:
        if _attr(b, "type") != "text":
            continue
        t = _attr(b, "text") or ""
        cites = _attr(b, "citations") or []
        start = len(full)
        full += t
        seg_spans.append((start, start + len(t), list(cites)))

    facts: list[ExtractedFact] = []
    dropped_uncited = 0
    dropped_unverified = 0

    # 2. Walk logical lines; attach citations whose segment overlaps the line.
    pos = 0
    for line in full.splitlines(keepends=True):
        line_start, line_end = pos, pos + len(line)
        pos = line_end
        stripped = line.strip()
        if not stripped or _is_none_marker(stripped):
            continue

        line_cites: list[Any] = []
        for seg_start, seg_end, cites in seg_spans:
            if cites and seg_start < line_end and seg_end > line_start:
                line_cites.extend(cites)

        tag, rest = _split_tag(stripped)
        value = rest.strip()
        if not line_cites:
            if tag or len(stripped) > 8:
                # A substantive claim with no citation span — dropped, never emitted.
                dropped_uncited += 1
            continue

        cit = _citation_from(line_cites[0], source_doc_id, document_title)
        # Belt-and-suspenders literal-match gate (only where we hold the source text).
        if source_text is not None and not _cited_in_source(cit.cited_text, source_text):
            dropped_unverified += 1
            continue
        facts.append(ExtractedFact(field=tag or "fact", value=value or stripped, citation=cit))
    return facts, dropped_uncited, dropped_unverified


def facts_from_image_blocks(blocks: Any, *, source_doc_id: str) -> list[ExtractedFact]:
    """Image path: the API does not emit span citations for images, so each tagged
    line becomes an image-LEVEL fact (locus = the whole image) marked
    ``signal="image_visual"``. Honest about the weaker grounding — this is the
    VLM's description of what it sees, not a span into source bytes."""
    full = "".join((_attr(b, "text") or "") for b in (blocks or []) if _attr(b, "type") == "text")
    facts: list[ExtractedFact] = []
    for line in full.splitlines():
        stripped = line.strip()
        if not stripped or _is_none_marker(stripped):
            continue
        tag, rest = _split_tag(stripped)
        if not tag and len(stripped) <= 8:
            continue
        cit = Citation(
            source_doc_id=source_doc_id,
            locus=f"image:{source_doc_id}",
            kind="image",
            cited_text="",
        )
        facts.append(
            ExtractedFact(
                field=tag or "fact",
                value=(rest.strip() or stripped),
                citation=cit,
                signal="image_visual",
            )
        )
    return facts


# --------------------------------------------------------------------------- #
# Content assembly + orchestration (network boundary).
# --------------------------------------------------------------------------- #
def _doc_id(tenant_id: str, name: str) -> str:
    """Deterministic, tenant-namespaced source doc id."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "doc").lower()).strip("_")[:40] or "doc"
    return f"vlmdoc_{tenant_id}_{slug}"


def _build_content(
    data: bytes | str, media_type: str, source_kind: str, *, title: str
) -> list[dict[str, Any]]:
    """Assemble the user-message content: the document/image block first, then the
    extraction instruction (per the API's document-before-text ordering)."""
    if source_kind == "text":
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        return [
            {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": text},
                "title": title,
                "citations": {"enabled": True},
            },
            {"type": "text", "text": _USER_INSTRUCTION},
        ]
    if source_kind == "pdf":
        b64 = base64.standard_b64encode(bytes(data)).decode("ascii")
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
                "title": title,
                "citations": {"enabled": True},
            },
            {"type": "text", "text": _USER_INSTRUCTION},
        ]
    # image
    b64 = base64.standard_b64encode(bytes(data)).decode("ascii")
    return [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
        {"type": "text", "text": _IMAGE_INSTRUCTION},
    ]


def ingest_bytes(
    tenant_id: str,
    name: str,
    data: bytes | str,
    *,
    media_type: str | None = None,
    model: str | None = None,
    doc_id: str | None = None,
    max_tokens: int = 4096,
) -> IngestResult:
    """Ingest one document/image into grounded, cited facts for ``tenant_id``.

    Raises :class:`NotConfiguredError` when the format is unparseable (DOCX/PPTX/
    XLSX/unknown) or the model is unconfigured — fail-closed, never fabricated.
    Raises :class:`ValueError` for a missing tenant."""
    if not tenant_id or not str(tenant_id).strip():
        raise ValueError("tenant_id is required (tenant-scoped ingestion)")

    media_type = media_type or guess_media_type(name)
    source_kind = _source_kind(media_type)
    if source_kind is None:
        ext = Path(name or "").suffix.lower()
        family = _NEEDS_CONVERTER.get(ext)
        if family:
            fmt = ext.lstrip(".").upper()  # e.g. DOCX
            raise NotConfiguredError(
                f"{fmt} unsupported: no {fmt}->pdf/text converter is configured "
                f"(file {name!r}, {family}). Convert it to PDF or plain text first "
                "(tabular data belongs in the CSV path of studio.documents). "
                "Extraction is never fabricated from an unparsed format. "
                "FOLLOW-UP: operator sends real offers docs as word/pdf/docx — a "
                f"{fmt}->text converter is a fast-follow, not part of this slice."
            )
        raise NotConfiguredError(
            f"no parser configured for {name!r} (media_type={media_type!r}). "
            "Natively supported: PDF, plain text/markdown, and PNG/JPEG/WebP/GIF "
            "images. No extraction is fabricated for an unsupported format."
        )

    client = _client()  # raises NotConfiguredError when unconfigured
    model = model or _default_model()
    doc_id = doc_id or _doc_id(tenant_id, name)
    content = _build_content(data, media_type, source_kind, title=name)

    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    blocks = _attr(message, "content", []) or []

    dropped_uncited = 0
    dropped_unverified = 0
    if source_kind == "image":
        facts = facts_from_image_blocks(blocks, source_doc_id=doc_id)
    else:
        # We hold the source text only for the text/markdown path — pass it so the
        # literal-match gate runs there; PDF text lives server-side, so source_text=None.
        source_text = (
            data.decode("utf-8")
            if source_kind == "text" and isinstance(data, (bytes, bytearray))
            else (str(data) if source_kind == "text" else None)
        )
        facts, dropped_uncited, dropped_unverified = facts_from_blocks(
            blocks, source_doc_id=doc_id, document_title=name, source_text=source_text
        )
    return IngestResult(
        tenant_id=tenant_id,
        source_doc_id=doc_id,
        source_kind=source_kind,
        model=model,
        facts=facts,
        dropped_uncited=dropped_uncited,
        dropped_unverified=dropped_unverified,
    )


def ingest_file(
    tenant_id: str, path: str | Path, *, model: str | None = None, **kwargs: Any
) -> IngestResult:
    """Ingest a file from disk (media type inferred from the extension)."""
    p = Path(path)
    return ingest_bytes(tenant_id, p.name, p.read_bytes(), model=model, **kwargs)
