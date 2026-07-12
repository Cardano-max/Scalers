"""Typed per-tenant pack schema (INFRA-04, systemdesign §5.3).

A *pack* is the per-tenant config that makes the generic engine adapt to a client
niche without code changes. The frontend is generic; the niche lives here:
brand-voice refs, the channel set, per-channel autonomy defaults, rate caps,
suppression source, sending domain, schedule, and which research sources are
enabled.

Everything is a Pydantic v2 model so a malformed pack fails validation on load
rather than surfacing as a surprise at run time. Secrets are never inlined — a
:class:`SecretRef` names an environment variable, resolved at access.
"""

from __future__ import annotations

import os
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Channel(str, Enum):
    """A surface the engine can act on for a tenant."""

    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    GMAIL = "gmail"


class AutonomyMode(str, Enum):
    """How much the engine may act on a channel without a human.

    * ``AUTO`` — act automatically when confidence clears the threshold.
    * ``REVIEW`` — always queue for operator approval (approve-first).
    * ``OFF`` — channel disabled; the engine produces nothing for it.
    """

    AUTO = "auto"
    REVIEW = "review"
    OFF = "off"


# The decision an autonomy config yields for a given confidence.
AutonomyDecision = str  # Literal["auto", "review", "off"] at the call sites


class SecretRef(BaseModel):
    """A pointer to a secret, by environment variable name.

    The secret value is never stored in a pack file — only the variable name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    env: str = Field(description="Name of the environment variable holding the secret.")

    def resolve(self) -> str | None:
        """Read the secret from the environment, or ``None`` if unset."""
        return os.environ.get(self.env)

    def require(self) -> str:
        """Read the secret, raising ``KeyError`` if it is not set."""
        value = self.resolve()
        if value is None:
            raise KeyError(f"secret env var {self.env!r} is not set")
        return value


class VoiceRef(BaseModel):
    """Reference to the brand-voice skill/examples for a tenant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill: str = Field(description="Brand-voice skill ref id (loaded on demand by the engine).")
    examples_uri: str | None = Field(
        default=None, description="Optional URI to a voice-example set for similarity checks."
    )
    positioning: str | None = Field(
        default=None,
        description=(
            "One-line, honest positioning of the studio (e.g. 'a Brooklyn fine-line "
            "tattoo studio'). Used by config.loader.describe_tenant to ground prompts "
            "in the tenant's REAL identity — never a hardcoded fabrication. None (no "
            "positioning on file) degrades to the bare handle, never an invented niche."
        ),
    )


class AutonomyConfig(BaseModel):
    """Per-channel autonomy defaults: mode plus the confidence gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: AutonomyMode = AutonomyMode.REVIEW  # safe default: approve-first
    threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in [0,1] required to auto-act when mode=auto.",
    )

    def decision(self, confidence: float) -> AutonomyDecision:
        """Pure decision for a produced action at a given confidence.

        This is the seam that lets a pack value change behavior with no code
        change: flip ``mode`` or move ``threshold`` and the same confidence input
        routes differently. The graph's HARN-05 router consumes the same signals;
        this method is the tenant-config view of them.
        """
        if self.mode is AutonomyMode.OFF:
            return "off"
        if self.mode is AutonomyMode.REVIEW:
            return "review"
        # AUTO: gate on confidence.
        return "auto" if confidence >= self.threshold else "review"


class RateLimits(BaseModel):
    """Per-channel rate caps. ``None`` means "no explicit cap in config"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    per_hour: int | None = Field(default=None, ge=0)
    per_day: int | None = Field(default=None, ge=0)


class ChannelConfig(BaseModel):
    """Everything tenant-specific about one channel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    limits: RateLimits = Field(default_factory=RateLimits)


class SuppressionConfig(BaseModel):
    """Where the suppression list (do-not-contact) comes from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(description="Ref/URI to the suppression list source.")


class ScheduleConfig(BaseModel):
    """Posting cadence and quiet hours for a tenant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timezone: str = "UTC"
    posts_per_day: int = Field(default=1, ge=0)
    quiet_hours: tuple[int, int] | None = Field(
        default=None, description="Local [start, end) hour range to suppress sends, 0-23."
    )

    @field_validator("quiet_hours")
    @classmethod
    def _hours_in_range(cls, v: tuple[int, int] | None) -> tuple[int, int] | None:
        if v is not None and not all(0 <= h <= 23 for h in v):
            raise ValueError(f"quiet_hours must be hours in 0-23, got {v}")
        return v


class ResearchConfig(BaseModel):
    """Which external research sources are enabled for a tenant, and which
    provider leads the research fan-out.

    ``provider`` is the PRIMARY research backend the engine reaches for first —
    the client (PA meeting, 2026-07-11) directed us onto Anthropic-powered
    research (Claude ``claude-fable-5`` for the hardest strategy, with a
    server-side fallback) instead of the free Firecrawl path, so it defaults to
    ``"anthropic"``. It must name a provider registered in
    ``research.default_registry`` / ``research.pipeline.live_registry``; an
    unknown name is dropped by the router with a note (never a silent live call),
    so the fan-out simply degrades to the other enabled ``sources``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[str, ...] = ()
    provider: str = Field(
        default="anthropic",
        description=(
            "Primary research provider name (must match a registered SourceProvider). "
            "'anthropic' leads with Claude web research; the other 'sources' remain "
            "available as the router's fan-out set."
        ),
    )


class CompetitorDiscoveryConfig(BaseModel):
    """How competitor-post discovery selects and scores who to mold from.

    The client's core note (PA meeting, 2026-07-11): the old discovery was
    HASHTAG-driven and surfaced small accounts (~100 likes) instead of the real
    top performers (20k–50k+ likes). This config drives the broader, ToS-compliant
    discovery + the deterministic scorer:

    * ``styles`` — the visual styles that define the niche ("black and grey
      realism", "fine line botanical", "color realism"). Discovery matches on
      these + location, NOT on hashtag presence, so a strong post with no tags
      still qualifies.
    * ``location`` — the geography to bias discovery toward (studio city or a
      broader market); empty falls back to the pack positioning / plan.
    * ``min_followers`` / ``min_engagement_rate`` — floors that keep tiny accounts
      out of the mold set; ``None`` means "no floor" (honest: an absent metric is
      never treated as a zero that fails the floor). ``min_engagement_rate`` is a
      FRACTION in ``[0, 1]`` — ``0.02`` means 2% — matching the discovery-stored
      ``(likes+comments)/followers`` ratio; a value above 1 is rejected at load so a
      "2.0 meaning 2%" slip fails LOUD instead of silently dropping every account.
    * ``hashtag_gated`` — kept for the legacy behavior; defaults ``False`` (the new
      broader logic). Set ``True`` only to pin the old hashtag-first matching.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    styles: tuple[str, ...] = ()
    location: str | None = None
    min_followers: int | None = Field(default=None, ge=0)
    min_engagement_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    limit_handles: int = Field(default=10, ge=1)
    time_budget_s: float = Field(default=60.0, gt=0.0)
    hashtag_gated: bool = False


class BrandStudyConfig(BaseModel):
    """Cross-industry marketing intelligence: study top brands BEYOND the niche.

    The client asked us to keep tattooing as the base but "go far beyond that" —
    study how the hottest brands in the US/EU (he named Skims) win attention:
    their hooks, theories, philosophies, and which objective they optimize for
    (follower growth vs engagement vs sales). The molder blends these
    cross-industry hooks into its angle/hook selection.

    Disabled by default so an un-configured tenant keeps the tattoo-only behavior;
    ``industries`` / ``seed_brands`` scope the study when enabled.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    industries: tuple[str, ...] = ()
    seed_brands: tuple[str, ...] = ()
    objectives: tuple[str, ...] = ("followers", "engagement", "sales")
    max_brands: int = Field(default=8, ge=1)


class InkPulseConfig(BaseModel):
    """Ingestion source for Ink Pulse leads — the pre-CRM conversation platform.

    The client's studio talks to prospects in "Ink Pulse" before they ever reach
    the CRM (the CRM only holds booked / deposit-pending clients). Those
    consultation leads (name / email / phone / instagram / conversation history)
    are lost to the campaign engine today. This config points the ingestion
    connector at that feed; disabled by default. The secret (API key) is NOT
    inlined — it rides the pack's ``[secrets.*]`` table via a ``SecretRef``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    source: str | None = Field(
        default=None,
        description="Ref/URI or feed id for the Ink Pulse export (CSV/JSON/API).",
    )
    api_key: SecretRef | None = Field(
        default=None,
        description="Secret ref for the Ink Pulse API key (env var name), never inlined.",
    )


class MetaPixelConfig(BaseModel):
    """Meta Pixel / Conversions-API groundwork — audience-commonality signals.

    The client wants Pixel-style tracking of where the audience goes and what they
    buy/like, to inform targeting — a DIFFERENT layer from the per-lead deep
    research. This is scaffolding + feasibility (confirm with Muaraf before the
    next meeting): disabled by default, ``pixel_id`` optional, token via
    ``SecretRef`` (per-tenant ``<TENANT>_META_PIXEL_TOKEN`` convention), never
    inlined. No live Pixel call fires while ``enabled`` is False.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    pixel_id: str | None = None
    access_token: SecretRef | None = Field(
        default=None,
        description="Secret ref for the Meta Pixel / Conversions-API token (env var name).",
    )


class TenantPack(BaseModel):
    """The full typed config for one tenant."""

    # Reject unknown keys: a typo in a pack file should fail loudly, not be ignored.
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(
        min_length=1, description="Stable tenant key; matches the pack filename."
    )
    display_name: str
    voice: VoiceRef
    channels: dict[Channel, ChannelConfig] = Field(
        default_factory=dict, description="Channel set + per-channel config."
    )
    suppression: SuppressionConfig | None = None
    sending_domain: str | None = Field(
        default=None, description="Dedicated cold-email sending domain (never the client's main domain)."
    )
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    # Client-directed additions (PA meeting 2026-07-11). All optional-with-defaults
    # so existing packs validate unchanged; a tenant opts in by adding the table.
    competitor_discovery: CompetitorDiscoveryConfig = Field(
        default_factory=CompetitorDiscoveryConfig
    )
    brand_study: BrandStudyConfig = Field(default_factory=BrandStudyConfig)
    ink_pulse: InkPulseConfig = Field(default_factory=InkPulseConfig)
    meta_pixel: MetaPixelConfig | None = None
    secrets: dict[str, SecretRef] = Field(default_factory=dict)

    # -- convenience accessors the engine uses at run start ------------------ #

    def channel(self, channel: Channel) -> ChannelConfig:
        """Config for ``channel``, falling back to defaults if unspecified."""
        return self.channels.get(channel, ChannelConfig())

    def is_enabled(self, channel: Channel) -> bool:
        cfg = self.channels.get(channel)
        return bool(cfg and cfg.enabled and cfg.autonomy.mode is not AutonomyMode.OFF)

    def autonomy_for(self, channel: Channel) -> AutonomyConfig:
        """The autonomy config (mode + threshold) for ``channel``, with defaults."""
        return self.channel(channel).autonomy

    def enabled_channels(self) -> tuple[Channel, ...]:
        return tuple(c for c in self.channels if self.is_enabled(c))
