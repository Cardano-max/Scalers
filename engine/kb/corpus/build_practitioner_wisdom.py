"""Build the practitioner-wisdom JSONL from the curated source markdown.

VERBATIM is the asset (bead 1mk.9 / operator insight): re-wording reintroduces
AI tells, so the human sentences must reach the KB EXACTLY as written. This
generator *mechanically lifts* the quoted text out of the curated source docs —
it never retypes or paraphrases a quote — and a round-trip check
(``--verify``) asserts every emitted ``text`` is a literal substring of its
source file. If a quote can't be found verbatim in the source, the build fails
loudly rather than shipping a drifted sentence.

Two sources, both vendored under ``sources/`` for provenance:
  * ``winning-strategies-kb.md`` — verbatim practitioner quotes, already
    de-duped + categorized by the R&D harvest (general / brand-voice /
    hooks-CTA / research / reply / outreach).
  * ``skills-dos-donts.md``      — DO / DON'T rules distilled from those quotes
    (kind=distilled-rule), each carrying its bracketed source attribution.

Output: ``practitioner_wisdom.jsonl`` — one global, source-attributed,
categorized grounding row per line, ready for ``kb.grounding`` to embed + load
into the ``practitioner_wisdom`` partition once the KB tables exist (rvy.2).

Run:  python -m kb.corpus.build_practitioner_wisdom            # (from engine/)
      python kb/corpus/build_practitioner_wisdom.py --verify   # check only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HARVESTED_AT = "2026-06-28"  # date of the R&D harvest; passed in (no Date.now in build)
PARTITION = "practitioner-wisdom"

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
WINNING = SOURCES / "winning-strategies-kb.md"
DOSDONTS = SOURCES / "skills-dos-donts.md"
OUT = HERE / "practitioner_wisdom.jsonl"

# Markdown "## <name> (...)" section header -> canonical category slug.
CATEGORY_BY_HEADING = {
    "general": "general",
    "brand-voice": "brand-voice",
    "hooks-cta": "hooks-cta",
    "research": "research",
    "reply": "reply",
    "outreach": "outreach",
}

# Threads referenced in the harvest header (for source.thread_topic enrichment).
THREAD_TOPIC = {
    "T1": ("r/DigitalMarketing", "Claude skills for digital marketers"),
    "T2": ("r/AskMarketing", "What Claude Skills have you built that are genuinely useful for marketing?"),
    "T3": ("r/ClaudeAI", "20 Claude Skills for Marketing, Launch and Sales built for technical people"),
    "T4": ("r/ClaudeAI", "What are the 'Must-Have' Claude Skills for marketers in 2026?"),
    "OP-NOTES": (None, "operator's own notes at top of file"),
}

# A bullet quote:  - "....quote (may contain \" escapes)...." — Attribution tail
# Non-greedy body, anchored to the closing `" —` so embedded quotes survive.
_QUOTE_RE = re.compile(r'^-\s+"(?P<quote>.+?)"\s+—\s+(?P<attrib>.+?)\s*$')
# A DO/DON'T bullet:  - Rule text [optional bracket attribution]
_RULE_RE = re.compile(r"^-\s+(?P<rule>.+?)\s*$")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_id(category: str, content_hash: str) -> str:
    return f"pw-{category}-{content_hash[:12]}"


def _classify_kind(attrib: str) -> str:
    low = attrib.lower()
    if "op-notes" in low:
        return "operator-note"
    if "curated skill description" in low:
        return "curated-skill-description"
    return "testimonial"


def _detect_language(quote: str, attrib: str) -> str:
    return "fr" if "french" in attrib.lower() else "en"


def _parse_attrib(attrib: str) -> dict:
    """Split 'Author, Tn (note)' into author + thread token + note (kept loose;
    the raw attribution is always preserved in source.attribution_raw)."""
    note = None
    m = re.search(r"\(([^)]+)\)\s*$", attrib)
    core = attrib
    if m:
        note = m.group(1)
        core = attrib[: m.start()].strip()
    thread = None
    author = core
    cm = re.search(r",\s*(T[1-4]|OP-NOTES)\s*$", core)
    if cm:
        thread = cm.group(1)
        author = core[: cm.start()].strip()
    else:
        # operator notes are attributed as "OP-NOTES (...)" with no comma
        if core.strip().upper().startswith("OP-NOTES"):
            thread = "OP-NOTES"
            author = "operator"
    return {"author": author or None, "thread": thread, "note": note}


def _quote_entries() -> list[dict]:
    entries: list[dict] = []
    category: str | None = None
    in_applicability_note = False
    for raw in WINNING.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        h = re.match(r"^##\s+([^\s(]+)", line)
        if h:
            key = h.group(1).strip().lower()
            category = CATEGORY_BY_HEADING.get(key)
            # The trailing "## NOTE on ..." section is prose, not entries.
            in_applicability_note = category is None
            continue
        if category is None or in_applicability_note:
            continue
        qm = _QUOTE_RE.match(line)
        if not qm:
            continue
        # The source escapes embedded quotes as \" — unescape to the literal the
        # human actually wrote (the verify pass re-escapes to confirm provenance).
        quote = qm.group("quote").replace('\\"', '"')
        attrib = qm.group("attrib")
        parsed = _parse_attrib(attrib)
        thread = parsed["thread"]
        subreddit, topic = THREAD_TOPIC.get(thread, (None, None))
        chash = _hash(quote)
        entries.append(
            {
                "id": _stable_id(category, chash),
                "partition": PARTITION,
                "scope": "GLOBAL",
                "category": category,
                "kind": _classify_kind(attrib),
                "text": quote,
                "language": _detect_language(quote, attrib),
                "source": {
                    "author": parsed["author"],
                    "thread": thread,
                    "subreddit": subreddit,
                    "thread_topic": topic,
                    "note": parsed["note"],
                    "attribution_raw": attrib,
                    "doc": "winning-strategies-kb.md",
                },
                "content_hash": chash,
                "harvested_at": HARVESTED_AT,
            }
        )
    return entries


def _rule_entries() -> list[dict]:
    """DO/DON'T rules — distilled, but each kept exactly as written with its
    bracketed source attribution. category is 'do' or 'dont'."""
    entries: list[dict] = []
    polarity: str | None = None  # 'do' | 'dont'
    subsection: str | None = None
    for raw in DOSDONTS.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        hh = re.match(r"^##\s+(.+?)\s*$", line)
        if hh:
            name = hh.group(1).strip().upper()
            if name == "DO":
                polarity = "do"
            elif name in ("DON'T", "DONT", "DON’T"):
                polarity = "dont"
            else:
                polarity = None
            continue
        sm = re.match(r"^###\s+(.+?)\s*$", line)
        if sm:
            subsection = sm.group(1).strip()
            continue
        if polarity is None:
            continue
        rm = _RULE_RE.match(line)
        if not rm:
            continue
        rule = rm.group("rule").strip()
        if not rule:
            continue
        # Pull a trailing/inner [attribution] if present (kept in text too).
        bracket = None
        bm = re.search(r"\[([^\]]+)\]\s*$", rule)
        if bm:
            bracket = bm.group(1)
        chash = _hash(rule)
        entries.append(
            {
                "id": _stable_id(polarity, chash),
                "partition": PARTITION,
                "scope": "GLOBAL",
                "category": polarity,
                "kind": "distilled-rule",
                "text": rule,
                "language": "en",
                "source": {
                    "author": None,
                    "thread": None,
                    "subreddit": None,
                    "thread_topic": None,
                    "note": subsection,
                    "attribution_raw": bracket,
                    "doc": "skills-dos-donts.md",
                },
                "content_hash": chash,
                "harvested_at": HARVESTED_AT,
            }
        )
    return entries


def build() -> list[dict]:
    entries = _quote_entries() + _rule_entries()
    # De-dup on content_hash within a category (defensive; the harvest is
    # already de-duped). Keep first occurrence, preserving distinct phrasings.
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for e in entries:
        key = (e["category"], e["content_hash"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def verify(entries: list[dict]) -> list[str]:
    """Every quote-derived text MUST appear verbatim in its source doc (modulo
    the source's \\" escaping). Returns a list of provenance failures."""
    winning = WINNING.read_text(encoding="utf-8")
    dosdonts = DOSDONTS.read_text(encoding="utf-8")
    failures: list[str] = []
    for e in entries:
        doc = e["source"]["doc"]
        hay = winning if doc == "winning-strategies-kb.md" else dosdonts
        text = e["text"]
        # Sources escape embedded double-quotes; re-escape before the substring
        # check so the provenance test matches the on-disk bytes.
        if text in hay or text.replace('"', '\\"') in hay:
            continue
        failures.append(f"{e['id']}: text not found verbatim in {doc}: {text[:70]!r}")
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build practitioner-wisdom JSONL (verbatim).")
    ap.add_argument("--verify", action="store_true", help="check provenance only; do not write")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    entries = build()
    failures = verify(entries)
    if failures:
        print("VERBATIM PROVENANCE FAILED:", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)
        return 1

    by_cat: dict[str, int] = {}
    for e in entries:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items()))
    print(f"{len(entries)} verbatim entries ({summary}) — provenance OK")

    if args.verify:
        return 0

    with args.out.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
