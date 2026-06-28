"""Canonical PostDraft / MediaSpec schema (Phase-3 ADR PR #38).

The typed output of the copywriter/draft cell (a9m.5) and the input the media/
format validators (a9m.6) check. Defined here as the one shared contract so the
draft cell and the validators build to identical types (the ADR is the source).

Phase-3 mock generates **no real media** — ``MediaSpec.brief`` describes what the
asset should show; the validators check the *spec* (kind/aspect/duration) +
caption/hashtags. Real-media facts (JPEG bytes, codec/container, file size) are
the real-publisher's job (Phase-6), not representable in this spec.
"""

from __future__ import annotations

# Canonical home is cells/post_schemas.py (the neutral leaf). Re-exported here for
# back-compat with existing importers (a9m.6 validators). ONE MediaKind/MediaSpec/
# PostDraft object across the engine — see post_schemas for why (identity checks).
from cells.post_schemas import MediaKind, MediaSpec, PostDraft

__all__ = ["MediaKind", "MediaSpec", "PostDraft"]
