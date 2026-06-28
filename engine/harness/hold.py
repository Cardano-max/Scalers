"""bead-439 autonomy HOLD — fail-safe, code-enforced (CustomerAcq-b3f).

The 439 hold was doc-only; this makes it a deterministic code control. Until the
real Phase-5 stack lands (a real cross-family jury, a *computed* confidence, a
real embedder, and a real human gold set), the autonomy stack is stubbed — the
jury returns agreement 1.0 with no model call, confidence is hardcoded 0.9, the
embedder is SHA-256, and the gold set is mock. None of those may gate an
auto-fire. So the system is **HELD by default**: a tenant/channel auto-fires only
after an operator EXPLICITLY lifts the hold for it.

:class:`HoldRegistry` is the single source of held-ness. It is fail-safe by
construction: ``is_held`` returns ``True`` unless an explicit lift is recorded.
The router enforces the hold (``AutonomyMode.HOLD`` -> ``REVIEW``); this registry
decides *who* is held.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.state import AutonomyMode


@dataclass(frozen=True)
class HoldRegistry:
    """Fail-safe registry of bead-439 autonomy lifts.

    A lift is keyed ``(tenant_id, channel)`` for a single channel, or
    ``(tenant_id, None)`` to lift a whole tenant. Anything not explicitly lifted
    is HELD — the safe default that prevents a forgotten/new tenant or channel
    from auto-firing.
    """

    lifted: frozenset[tuple[str, str | None]] = field(default_factory=frozenset)

    def is_held(self, tenant_id: str, channel: str | None = None) -> bool:
        """True unless ``tenant_id`` (whole-tenant) or ``(tenant_id, channel)`` is lifted."""
        if (tenant_id, None) in self.lifted:
            return False
        if channel is not None and (tenant_id, channel) in self.lifted:
            return False
        return True  # FAIL-SAFE: held by default.

    def effective_autonomy(
        self, pack_autonomy: AutonomyMode, tenant_id: str, channel: str | None = None
    ) -> AutonomyMode:
        """The dial actually fed to the router: ``HOLD`` if held, else the pack dial.

        HOLD overrides the per-tenant pack dial (vvi/2kp/epq) — a held tenant is
        review-only no matter what its pack says.
        """
        if self.is_held(tenant_id, channel):
            return AutonomyMode.HOLD
        return pack_autonomy

    def lift(self, tenant_id: str, channel: str | None = None) -> "HoldRegistry":
        """Return a new registry with ``(tenant_id, channel)`` additionally lifted."""
        return HoldRegistry(lifted=self.lifted | {(tenant_id, channel)})


# Process-default registry: an EMPTY lift set means EVERYTHING is held. This is
# the Phase-2 reality — nothing may auto-fire while the autonomy stack is stubbed.
DEFAULT_HOLD_REGISTRY = HoldRegistry()
