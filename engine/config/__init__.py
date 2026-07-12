"""Per-tenant config / packs (INFRA-04).

The generic engine adapts to a client niche through a typed per-tenant *pack*
(:class:`~config.schema.TenantPack`) loaded at run start by
:func:`~config.loader.load_pack`. The frontend stays generic; the niche lives
here.
"""

from config.loader import (
    DEFAULT_PACKS_DIR,
    PackError,
    PackLoader,
    PackNotFoundError,
    PackParseError,
    PackValidationError,
    available_tenants,
    load_pack,
)
from config.schema import (
    AutonomyConfig,
    AutonomyMode,
    BrandStudyConfig,
    Channel,
    ChannelConfig,
    CompetitorDiscoveryConfig,
    InkPulseConfig,
    MetaPixelConfig,
    RateLimits,
    ResearchConfig,
    ScheduleConfig,
    SecretRef,
    SuppressionConfig,
    TenantPack,
    VoiceRef,
)

__all__ = [
    # loader
    "DEFAULT_PACKS_DIR",
    "PackError",
    "PackLoader",
    "PackNotFoundError",
    "PackParseError",
    "PackValidationError",
    "available_tenants",
    "load_pack",
    # schema
    "AutonomyConfig",
    "AutonomyMode",
    "BrandStudyConfig",
    "Channel",
    "ChannelConfig",
    "CompetitorDiscoveryConfig",
    "InkPulseConfig",
    "MetaPixelConfig",
    "RateLimits",
    "ResearchConfig",
    "ScheduleConfig",
    "SecretRef",
    "SuppressionConfig",
    "TenantPack",
    "VoiceRef",
]
