"""SMOKE gold set (rvy.10 / EVAL) — synthetic, deterministically labeled.

Purpose: exercise the eval pipeline (loaders -> scorers -> eval_metric -> CI
gate) end-to-end NOW, before the real human-labeled gold sets (rvy.4/.5/.6) — so
rvy.7 (Inspect suite) + rvy.8 (calibration gates) can be built and demoed, and
rvy.9 has a dataset to seed a regression against.

NON-GATE: passing on SMOKE proves the machinery RUNS — never that the engine is
on-voice, accurate, or calibrated. It does NOT satisfy any real quality/autonomy
gate (the 439 hold stays until REAL eval + calibration on the real artist gold
set). SMOKE lives on the TEST tenant only and is filterable out of real-gate
queries via ``split``.

Format follows docs/eval/labeling-protocol.md §2 + §4 (the gold_example shape from
the rvy.1 ADR), loaded into the rvy.2 KB. Ground truth is the deterministic
``expected`` payload on each example (what scorers read); one ``smoke-oracle``
gold_label per example also exercises the per-rater path. Every record is tagged
``split=SMOKE`` so a real holdout/train query returns zero smoke rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kb.schema import Engine, Split

# ── Constants (the interface rvy.7 / rvy.8 / rvy.9 point at) ──────────────────
SMOKE_TENANT = "ladies8391"   # TEST account only — never a real client tenant
SMOKE_SPLIT = Split.SMOKE
LABEL_VERSION = 1
ORACLE_RATER = "smoke-oracle"
CREATED_BY = "rvy.10-smoke"

# Posting voice dimensions / engagement+outreach dims (labeling-protocol §4).
# Posting headline dimension is brand-voice (binary on/off), per pmm's mapping.
_POSTING_DIMS = ["brand-voice"]
_OUTREACH_DIMS = ["extraction", "personalization"]
_ENGAGEMENT_DIMS = ["triage_class", "reply_safety"]


@dataclass(frozen=True)
class SmokeExample:
    """One synthetic gold example with a deterministic, engineered label.

    ``hard``  — a hard near-miss / hard-band case (labeling-protocol §3).
    ``flip``  — designated metric-flip example: a deliberately-broken cell flips a
                metric on it, so rvy.9 can seed a regression and watch CI go red.
    """

    engine: Engine
    cell: str
    slug: str
    input: dict[str, Any]
    expected: dict[str, Any]
    dimensions: list[str] = field(default_factory=list)
    hard: bool = False
    flip: bool = False
    note: str = ""     # in-memory traceability only (not stored in the scored row)
    source: str = ""   # e.g. pmm positioning id (pmm/positioning/ladies8391/examples.jsonl)


def _inp(base: dict[str, Any], confidence: float | None) -> dict[str, Any]:
    """Attach an optional synthetic ``recorded_confidence`` for rvy.8's ECE gate.

    Phase 2 has no real per-example confidence (AUTON-02 is Phase 5), so this is a
    deterministic synthetic value the calibration gate can read instead of
    synthesizing — present on a few rows only.
    """
    if confidence is not None:
        return {**base, "recorded_confidence": confidence}
    return base


def _post(slug, text, on_voice, *, hard=False, flip=False, confidence=None, note="", source="") -> SmokeExample:
    # expected carries BOTH on_voice (bool, what rvy.7/rvy.8 score) and voice
    # (string, pmm's label_payload.voice) so either consumer reads its shape.
    return SmokeExample(
        engine=Engine.POSTING, cell="copywriter", slug=slug,
        input=_inp({"kind": "caption", "text": text}, confidence),
        expected={"on_voice": on_voice, "voice": "on_voice" if on_voice else "off_voice"},
        dimensions=_POSTING_DIMS, hard=hard, flip=flip, note=note, source=source,
    )


def _out(slug, text, name, role, company, hook, personalization, *, hard=False, flip=False, confidence=None) -> SmokeExample:
    return SmokeExample(
        engine=Engine.OUTREACH, cell="prospect_extract", slug=slug,
        input=_inp({"kind": "prospect_blurb", "text": text}, confidence),
        expected={
            "extraction": {"prospect_name": name, "role": role, "company": company, "hook": hook},
            "personalization": personalization,
        },
        dimensions=_OUTREACH_DIMS, hard=hard, flip=flip,
    )


def _eng(slug, channel, text, triage, safety, *, hard=False, flip=False, confidence=None) -> SmokeExample:
    return SmokeExample(
        engine=Engine.ENGAGEMENT, cell="triage", slug=slug,
        input=_inp({"kind": "engagement", "channel": channel, "text": text}, confidence),
        expected={"triage_class": triage, "reply_safety": safety},
        dimensions=_ENGAGEMENT_DIMS, hard=hard, flip=flip,
    )


# ── Posting: on-voice vs off-voice (binary) ──────────────────────────────────
# Voice ground truth is ladies8391 / "Ladies First" (woman-owned Austin
# neo-traditional color), grounded in pmm/positioning/ladies8391/. On-voice = the
# 5 authoritative pmm anchors + supplements in that register (first-person, client
# -first, approved claims, 0-2 approved emoji). Off-voice = the 3 pmm negatives +
# hard near-misses using the documented banned moves (girlboss hype, rule-of-three,
# unapproved claims, corporate "we"). NOTE: for a SMOKE set this overlaps the
# skill's grounding anchors deliberately to prove the pipeline — it is NOT a real
# measurement; the REAL holdout (rvy.4) must be disjoint from grounding (KNOW-02).
_POSTING: list[SmokeExample] = [
    # --- on-voice: pmm authoritative anchors (examples.jsonl) ---
    _post("p-onv-001", "She came in wanting to cover a scar she never chose. We spent the consult picking flowers that actually meant something to her. Now it's hers. 🌸 DM me to start your design.", True, flip=True, confidence=0.95, source="ladies8391-onv-001", note="reclaim-your-story"),
    _post("p-onv-002", "First tattoo? Good. We'll go slow. Free consult, no pressure to book — bring the idea even if you can't draw it, and ask me anything. No dumb questions.", True, source="ladies8391-onv-002", note="first-timer-friendly"),
    _post("p-onv-003", "Neo-traditional color lives or dies on saturation. Bold lines, packed color, built to still read bright once it's healed. That's the work. #neotraditionaltattoo #floraltattoo #austintattoo", True, source="ladies8391-onv-003", note="bold-and-feminine"),
    _post("p-onv-004", "This room is women-first on purpose. Private, appointment-only, no rush and no one talking over you. Bring your idea — we'll take our time. 🤍", True, source="ladies8391-onv-004", note="women-first-room"),
    _post("p-onv-005", "Floral half-sleeve we built over three sessions — peonies for her mom, wildflowers for where she grew up. Healed and settled now. 🌷 Consults are free — link in bio.", True, source="ladies8391-onv-005", note="reclaim-your-story"),
    # --- on-voice: supplements in the same register (grounded in brand-dna.md) ---
    _post("p-onv-006", "Cover-up consult this morning — she brought a scar she didn't choose and a photo of her grandmother's garden. We're drawing peonies, made for her. Consults are free. 🌸", True),
    _post("p-onv-007", "Topical numbing is there if you want it — just ask. I'd rather you sit comfortable than tough it out, and we take our time either way.", True),
    _post("p-onv-008", "Healed neo-traditional rose, color packed so it still reads bright months later. Drawn for her, no flash copies. #neotraditionaltattoo #floraltattoo #womentattooartist #austintattoo", True),
    _post("p-onv-009", "Nine years in and the consult is still my favorite part. No rush, no dumb questions — we don't book until the design feels like yours.", True),
    # --- off-voice: pmm authoritative negatives ---
    _post("p-offv-001", "Unleash your inner queen 👑✨ This stunning floral cover-up is bold, beautiful, and SO you. You deserve this, babe — treat yourself and slay. Book now! 🔥", False, hard=True, flip=True, confidence=0.90, source="ladies8391-offv-001", note="HARD near-miss: right topic, girlboss hype + banned emoji + rule-of-three + hard close"),
    _post("p-offv-002", "BEST tattoos in Austin, 100% painless guaranteed. $50 flash Friday — walk in today, no appointment needed! Voted #1 studio.", False, source="ladies8391-offv-002", note="unapproved claims: superlative + pain promise + price + walk-in"),
    _post("p-offv-003", "At Ladies First, we leverage industry-leading expertise to deliver premium, bespoke body-art solutions tailored to the modern woman. Contact our team to learn more.", False, hard=True, source="ladies8391-offv-003", note="corporate SaaS register, faceless 'we'"),
    # --- off-voice: hard near-misses (right topic, banned moves) ---
    _post("p-offv-004", "It's not just a tattoo, it's a statement. Bold, beautiful, timeless floral work that transforms your look.", False, hard=True, note="contrast framing + rule-of-three + banned 'transform your look'"),
    _post("p-offv-005", "Reclaim your power with bold, beautiful, unforgettable ink — because you deserve to feel like the main character. 🌸", False, hard=True, note="approved topic+emoji but girlboss 'main character'/'you deserve'/rule-of-three"),
    _post("p-offv-006", "Floral cover-up season is here 🌸 Bold color, fresh start, brand new you. Book your glow-up.", False, hard=True, note="approved emoji misused; 'brand new you'/'glow-up'/rule-of-three"),
    # --- off-voice: blatant (clear negatives) ---
    _post("p-offv-007", "Look no further for the #1 floral tattoos in Austin. Painless, affordable, walk-ins always welcome.", False, note="superlative + pain + price + walk-in"),
]

# ── Outreach: extraction ground truth + personalization (0/1/2) ──────────────
_OUTREACH: list[SmokeExample] = [
    _out("o-clean-01", "Hi, I'm Dana Brooks, Marketing Director at Lumen Skincare. Loved your studio's healed-work reel — the consistency is rare.",
         "Dana Brooks", "Marketing Director", "Lumen Skincare", "healed-work reel consistency", 2, flip=True, confidence=0.96),
    _out("o-clean-02", "Marcus Vue here, founder of Northside Barbers. We send a lot of clients your way and want to talk cross-promo.",
         "Marcus Vue", "Founder", "Northside Barbers", "cross-promo referral overlap", 2),
    _out("o-clean-03", "This is Priya Anand, Events Lead at Carrer Collective, reaching out about a flash-day pop-up at our space.",
         "Priya Anand", "Events Lead", "Carrer Collective", "flash-day pop-up at their space", 2),
    _out("o-clean-04", "I'm Theo Park, owner of Park & Co Coffee next door. Coffee-and-ink Saturdays could be fun for both of us.",
         "Theo Park", "Owner", "Park & Co Coffee", "coffee-and-ink Saturdays", 2),
    _out("o-clean-05", "Jess Romano, Brand Partnerships at Vellum Apparel — your blackwork aesthetic fits our fall lookbook perfectly.",
         "Jess Romano", "Brand Partnerships", "Vellum Apparel", "blackwork fits fall lookbook", 2),
    _out("o-shallow-01", "Hey it's Sam from Sam's Studio, just wanted to connect and see if you're open to collabs sometime.",
         "Sam", "Owner", "Sam's Studio", None, 1, hard=True),
    _out("o-shallow-02", "Hi, Robin Lee here. Reaching out to introduce myself and explore potential opportunities together.",
         "Robin Lee", None, None, None, 1, hard=True),
    # Generic mail-merge -> personalization 0
    _out("o-generic-01", "Dear Business Owner, we offer premium marketing services to grow your revenue. Reply to learn more.",
         None, None, None, None, 0, flip=True, confidence=0.88),
    _out("o-generic-02", "Hello, I hope this email finds you well. I'd love to discuss how we can add value to your business.",
         None, None, None, None, 0),
    _out("o-generic-03", "Quick question — are you the right person to talk to about boosting your online presence?",
         None, None, None, None, 0),
    # Hard extraction traps
    _out("o-hard-01", "It's Alex Kim — I split time between Kim Studios and the Kim Design Group, but reach me about the mural.",
         "Alex Kim", None, "Kim Studios", "mural collaboration", 1, hard=True),  # ambiguous company; role missing
    _out("o-hard-02", "Casey Nguyen, I handle a bit of everything at Bright Loft (marketing, ops, events). Let's chat partnerships.",
         "Casey Nguyen", None, "Bright Loft", "partnerships", 2, hard=True),     # role genuinely ambiguous -> None
    _out("o-hard-03", "Hi from the team at Orbit Media! We admire your work and would love to feature it. — The Orbit Team",
         None, None, "Orbit Media", "feature your work", 1, hard=True),          # no individual name
    _out("o-hard-04", "Jordan here at Flux. Possibly the wrong Flux you're thinking of — we're the gallery, not the gym.",
         "Jordan", None, "Flux", "gallery feature", 1, hard=True),               # wrong-company trap
    _out("o-hard-05", "Morgan Bailey, Studio Manager. (Company in signature below.) Hook: your aftercare content is the best I've seen.",
         "Morgan Bailey", "Studio Manager", None, "aftercare content praise", 2, hard=True),  # company missing
    _out("o-clean-06", "Lena Fischer, Creative Director at Saffron House. Your micro-realism portfolio is exactly our brand energy.",
         "Lena Fischer", "Creative Director", "Saffron House", "micro-realism matches brand", 2),
]

# ── Engagement: triage class + reply-safety (all DMs -> must-escalate) ────────
_ENGAGEMENT: list[SmokeExample] = [
    _eng("e-pos-01", "comment", "this is gorgeous, obsessed with the linework 😍", "positive", "safe-to-auto", flip=True, confidence=0.97),
    _eng("e-pos-02", "comment", "healed even better than day one, thank you!!", "positive", "safe-to-auto"),
    _eng("e-q-01", "comment", "how much for something this size on the forearm?", "question", "safe-to-auto"),
    _eng("e-q-02", "comment", "do you take walk-ins on weekends?", "question", "safe-to-auto"),
    _eng("e-lead-01", "comment", "I want to book a half sleeve, how do I start?", "lead", "safe-to-auto"),
    _eng("e-spam-01", "comment", "Make $5000/week from home!! click my bio link 🔥🔥", "spam", "safe-to-auto"),
    _eng("e-spam-02", "comment", "follow for follow? check out my page", "spam", "safe-to-auto"),
    _eng("e-comp-01", "comment", "waited 40 minutes past my appointment time, not okay.", "complaint", "must-escalate"),
    _eng("e-comp-02", "comment", "my tattoo is still red and swollen after a week, is that normal??", "complaint", "must-escalate", flip=True, confidence=0.62),
    # DMs: ALWAYS must-escalate regardless of class (protocol §4.3)
    _eng("e-dm-01", "dm", "hey do you have any openings this weekend?", "question", "must-escalate"),
    _eng("e-dm-02", "dm", "loved my piece, here's a photo of it healed!", "positive", "must-escalate"),
    _eng("e-dm-03", "dm", "I'd like to book a consultation for a large back piece.", "lead", "must-escalate"),
    # Hard band: sarcasm, veiled complaint, troll, ambiguous intent
    _eng("e-hard-01", "comment", "wow, ANOTHER skull tattoo, so original 🙄", "complaint", "must-escalate", hard=True),  # sarcasm, not positive
    _eng("e-hard-02", "comment", "must be nice to charge that much for a doodle", "complaint", "must-escalate", hard=True),  # veiled complaint
    _eng("e-hard-03", "comment", "your work is fine I guess if you're into that", "complaint", "must-escalate", hard=True),  # backhanded
    _eng("e-hard-04", "comment", "first 💀💀💀", "spam", "safe-to-auto", hard=True),  # low-value troll, safe to ignore
    _eng("e-hard-05", "comment", "is this even real or AI?", "question", "must-escalate", hard=True),  # ambiguous/skeptical -> human
    _eng("e-hard-06", "comment", "asking for a friend… do you fix other artists' bad work?", "lead", "must-escalate", hard=True),  # sensitive lead
]

_ALL: list[SmokeExample] = [*_POSTING, *_OUTREACH, *_ENGAGEMENT]


# ── Loader + query helpers (the rvy.7/.8/.9 entry points) ─────────────────────

def iter_smoke_examples() -> list[SmokeExample]:
    """All smoke examples (in-memory; no DB)."""
    return list(_ALL)


def metric_flip_examples() -> list[SmokeExample]:
    """The designated metric-flip examples for rvy.9's seeded regression.

    Each is an unambiguous case where a deliberately-broken cell flips a metric,
    so CI goes red on SMOKE data — proving the build-fail wiring works.
    """
    return [e for e in _ALL if e.flip]


def load_smoke_gold_set(store, tenant_id: str = SMOKE_TENANT) -> dict[str, int]:
    """Load the SMOKE gold set into the KB on ``tenant_id`` (idempotent).

    Upserts each example (natural key = tenant/engine/cell/content/version, so a
    re-load never duplicates) and writes one ``smoke-oracle`` gold_label carrying
    the deterministic expected payload. Returns per-engine counts.
    """
    counts: dict[str, int] = {}
    for ex in _ALL:
        example_id = store.upsert_gold_example(
            tenant_id=tenant_id,
            engine=ex.engine,
            cell=ex.cell,
            input=ex.input,
            expected=ex.expected,
            rubric_dimensions=ex.dimensions,
            split=SMOKE_SPLIT,
            label_version=LABEL_VERSION,
            created_by=CREATED_BY,
        )
        store.add_gold_label(
            example_id=example_id,
            tenant_id=tenant_id,
            rater_id=ORACLE_RATER,
            dimension="oracle",
            label=ex.expected,
            label_version=LABEL_VERSION,
        )
        counts[ex.engine.value] = counts.get(ex.engine.value, 0) + 1
    return counts


def get_smoke_set(store, engine: Engine, tenant_id: str = SMOKE_TENANT):
    """Fetch the SMOKE gold set for one engine (tenant-scoped, split=SMOKE)."""
    return store.get_gold_set(tenant_id=tenant_id, engine=engine, split=SMOKE_SPLIT)


def _main() -> int:
    """Load the SMOKE set into a live KB: ``ENGINE_DATABASE_URL=... python -m evals.smoke_gold_set``."""
    import os

    from kb.store import KbStore

    dsn = os.environ.get("ENGINE_DATABASE_URL")
    if not dsn:
        print("ENGINE_DATABASE_URL not set — point it at the eval KB Postgres.")
        return 2
    counts = load_smoke_gold_set(KbStore(dsn))
    total = sum(counts.values())
    print(f"loaded SMOKE gold set on tenant {SMOKE_TENANT!r} (split={SMOKE_SPLIT.value}): {counts} = {total} examples")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
