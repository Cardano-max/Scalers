"""Provider result of an executed side effect (kkg.3 / OBS-03).

A connector's ``send`` returns a :class:`ProviderResult` carrying the external
resource the console deep-links to — the post/message id, its URL, and a thread
reference. Real URLs land with the real Gmail/Meta MCP tooling (Phase 3/6); the
Phase-1 mock returns mock values to prove the capture mechanism. The shape does
not change when real tooling lands.

For backward compatibility a connector may still return a bare ``str``
(provider_id); :func:`as_provider_result` coerces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderResult:
    provider_id: str
    deep_link: str | None = None     # external_url to open in the console (null -> link disabled)
    external_id: str | None = None   # post id / message id at the provider
    thread_ref: str | None = None    # gmail thread / IG conversation reference
    extra: dict[str, Any] = field(default_factory=dict)

    def to_jsonb(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "deep_link": self.deep_link,
            "external_id": self.external_id,
            "thread_ref": self.thread_ref,
        }
        if self.extra:
            out.update(self.extra)
        return out


def as_provider_result(raw: "ProviderResult | str") -> ProviderResult:
    """Accept a rich ProviderResult or a bare provider_id string."""
    return raw if isinstance(raw, ProviderResult) else ProviderResult(provider_id=str(raw))
