"""Intent → channel pipeline router for the studio/voice supervisor (nmh.9, spec §16).

The supervisor must run the workflow the operator ASKED for — "send emails" → the
email pipeline, "create an Instagram post" → the IG pipeline, "Facebook campaign"
→ the FB pipeline, "run a campaign for this artist with attachments" → the
artist/artwork pipeline — instead of always running the email agents.

Today the one dispatcher (:func:`studio.agui._execute_campaign_sync`) branches ONLY
on ``lead_source == "provided"``; the compose spine ignores ``plan.channels`` and
every archetype bundles email, so email always runs. This module is the missing
router: a PURE, unit-testable decision over the plan's real fields.

HONESTY: a channel with no real supervisor-invoked run pipeline yet
(Facebook — not even a modeled channel; artist/artwork attachments — only a
standalone CLI drafter exists, nothing ingests attachments into a run) routes to a
``built=False`` decision so the caller returns an HONEST "that pipeline isn't built
yet" — never a fabricated email run dressed up as the requested one.

The router reads only plan fields (no I/O), so both the voice GO-gate and the chat
button get the identical routing. Note the voice ``update_plan`` tool can set only
``channels`` (+ goal/audience), so ``channels`` and ``goal`` are the primary intent
signal; the chat interview additionally sets ``campaign_type``/``action_type``/
``attach_artwork``, which sharpen the decision when present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

# The IG-first registered archetype an "Instagram post" intent runs through — a REAL
# IG drafting workflow (IG carousel/Reels), distinct from the email-outreach path.
_INSTAGRAM_ARCHETYPE = "artist_spotlight"


class Pipeline(str, Enum):
    """The channel pipeline an operator request routes to."""

    EMAIL = "email"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    ARTIST_ARTWORK = "artist_artwork"


# Which pipelines are BUILT as a supervisor-invoked run today (nmh.9 map):
#   email      — _execute_provided_leads_sync / gmail  → BUILT end-to-end
#   instagram  — compose spine IG archetypes            → BUILT (drafting, HELD)
#   facebook   — no channel, no archetype, no ads path  → NOT BUILT
#   artist/artwork — post_campaign.py standalone CLI    → NOT BUILT as a run
_BUILT: dict[Pipeline, bool] = {
    Pipeline.EMAIL: True,
    Pipeline.INSTAGRAM: True,
    Pipeline.FACEBOOK: False,
    Pipeline.ARTIST_ARTWORK: False,
}

_NOT_BUILT_REASON: dict[Pipeline, str] = {
    Pipeline.FACEBOOK: (
        "the Facebook campaign pipeline isn't built yet — Facebook isn't a modeled "
        "channel and there's no Facebook posting/ads workflow to run. I won't fake a "
        "run; upload the request as email or Instagram, or ask to have FB built."
    ),
    Pipeline.ARTIST_ARTWORK: (
        "the standalone artist/artwork campaign pipeline isn't built yet as its own "
        "run. Artwork attach IS available inside the built pipelines: run it as an "
        "Instagram post (the run pauses on the matching artwork picks for your "
        "choice), or as email outreach on your own uploaded leads with attach-artwork "
        "on. I won't fake a standalone artwork run."
    ),
}


@dataclass(frozen=True)
class RouteDecision:
    """The routing decision for a plan. ``built`` is False for a channel with no real
    supervisor-invoked pipeline yet — the caller returns an honest not-built response
    (no fabricated run). ``archetype_id`` is the compose archetype to force when the
    pipeline runs through the posting spine (Instagram)."""

    pipeline: Pipeline
    built: bool
    reason: str
    archetype_id: str | None = None

    @property
    def channel(self) -> str:
        return self.pipeline.value


def _text(plan: Any) -> str:
    """The lower-cased blob of every intent-bearing plan field."""
    parts: list[str] = []
    for attr in ("goal", "action_type", "campaign_type", "audience"):
        v = getattr(plan, attr, "") or ""
        if isinstance(v, str):
            parts.append(v)
    channels = getattr(plan, "channels", None) or []
    if isinstance(channels, (list, tuple)):
        parts.extend(str(c) for c in channels)
    return " ".join(parts).lower()


def _has(text: str, *words: str) -> bool:
    """Whole-word-ish match (word boundaries) for any of ``words`` in ``text``."""
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words)


def route_pipeline(plan: Any) -> RouteDecision:
    """Route ``plan`` to a channel pipeline from its real fields. Pure — no I/O.

    Priority (most specific intent first) — a stated social channel wins over the
    email default so "create an Instagram post" never runs the email agents:

      1. Facebook  → NOT BUILT (honest)
      2. lead_source=='provided' → BUILT (email) — the per-lead outreach compliance
         path is never bypassed by an incidental social word in the goal; with
         ``attach_artwork`` it now ALSO runs the artwork top-pick gate (item 3), so
         it must precede the artwork rule
      3. artist/artwork/attachments (with NO built channel chosen) → NOT BUILT (honest)
      4. Instagram/Reels/Story → BUILT (compose IG archetype + artwork gate)
      5. email/outreach → BUILT (email)
      6. default → BUILT (email) — backward-compatible with today's behaviour
    """
    text = _text(plan)
    attach_artwork = bool(getattr(plan, "attach_artwork", False))
    lead_source = (getattr(plan, "lead_source", "") or "").strip().lower()

    # 1. Facebook — explicit, and unbuildable in the current spine.
    if _has(text, "facebook", "fb", "messenger"):
        return RouteDecision(Pipeline.FACEBOOK, False, _NOT_BUILT_REASON[Pipeline.FACEBOOK])

    # 2. Provided leads (uploaded-CSV cohort) → the per-lead OUTREACH compliance path,
    #    BEFORE the social-channel AND artwork rules. lead_source='provided' means
    #    "contact MY uploaded people per-lead" — an incidental social word in the goal
    #    ("win back clients who follow us on instagram") never bypasses the
    #    consent-gated path (nmh.9 review S1), and ``attach_artwork`` on this cohort is
    #    BUILT now: the run pauses on the artwork top-picks for the operator's choice
    #    and attaches the selected piece to each staged draft (engine-core item 3).
    if lead_source == "provided":
        reason = (
            "uploaded-lead outreach — running the per-lead email pipeline against your "
            "own leads; nothing is sent (HELD)."
        )
        if attach_artwork:
            reason = (
                "uploaded-lead outreach with artwork attach — running the per-lead "
                "email pipeline; the run pauses on the matching artwork picks for "
                "your choice. Nothing is sent (HELD)."
            )
        return RouteDecision(Pipeline.EMAIL, True, reason)

    # 3. Artist/artwork ATTACHMENTS with no built channel chosen — the STANDALONE
    #    artist/artwork pipeline still isn't a supervisor-invoked run. Artwork attach
    #    IS built inside the two real pipelines (provided-lead email above; the IG
    #    spine's artwork gate below reaches it when the plan names Instagram WITHOUT
    #    artwork phrasing), so the honest not-built here points the operator at them.
    if attach_artwork or _has(text, "artwork", "attachment", "attachments", "portfolio"):
        return RouteDecision(
            Pipeline.ARTIST_ARTWORK, False, _NOT_BUILT_REASON[Pipeline.ARTIST_ARTWORK]
        )

    # 4. Instagram — a real IG drafting workflow via the compose spine. NOTE: pinned to
    #    the artist_spotlight archetype (a real IG/Reels/Email drafting path) so the trace
    #    proves IG ran; a dedicated generic `instagram_post` archetype is a follow-up.
    if _has(text, "instagram", "insta", "ig", "reel", "reels", "story", "stories"):
        return RouteDecision(
            Pipeline.INSTAGRAM,
            True,
            "Instagram post/story requested — running the IG drafting pipeline "
            f"(archetype {_INSTAGRAM_ARCHETYPE}); nothing is sent (HELD).",
            archetype_id=_INSTAGRAM_ARCHETYPE,
        )

    # 5. Email — explicit.
    if _has(text, "email", "emails", "outreach", "newsletter", "gmail"):
        return RouteDecision(
            Pipeline.EMAIL,
            True,
            "email/outreach requested — running the email pipeline; nothing is sent (HELD).",
        )

    # 5. Default — backward-compatible: the email/compose path today's callers expect.
    return RouteDecision(
        Pipeline.EMAIL,
        True,
        "no explicit channel intent — defaulting to the email pipeline; "
        "say 'Instagram post' or 'Facebook campaign' to route elsewhere.",
    )


def not_built_summary(
    decision: RouteDecision, *, run_id: str | None, campaign_id: str | None
) -> dict[str, Any]:
    """An HONEST run summary for a not-built pipeline — the shape the campaign
    dispatcher returns so the operator hears the truth and the DB/trace shows NO fake
    run (zero agent_runs, no runs row, a non-'completed' status). Never fabricates a
    completed campaign for a channel that cannot run."""
    return {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "routed_channel": decision.channel,
        "pipeline_built": False,
        "run_status": "not_built",
        "archetype_id": None,
        "agent_runs": [],
        "n_pending": 0,
        "n_queued": 0,
        "channels": [decision.channel],
        "runs_row": False,
        "message": decision.reason,
        "step_notes": [
            f"routed to the {decision.channel} pipeline, which isn't built yet — "
            "returned an honest not-built response (no run executed, nothing staged, "
            "nothing sent)."
        ],
        "failure_summary": [],
    }
