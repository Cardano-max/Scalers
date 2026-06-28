"""A mock side-effect connector for the exactly-once tests (Phase 1).

Stands in for the real Meta/Gmail MCP connector (Phase 6). It is **idempotent**:
calling :meth:`send` twice with the same ``key`` performs the external effect
exactly once and returns the same ``provider_id``. That keyed-idempotency is the
ONLY thing that makes exactly-once achievable for a non-transactional external
effect (the two-generals problem) — real IG/Gmail publish APIs provide it via an
idempotency token; here we model it so the contract is testable now.

Counters distinguish the two quantities a test cares about:

* ``effects`` / ``call_count`` — distinct keys that produced a real effect. This
  is THE exactly-once metric: it must be 1 no matter how many times a crash makes
  us retry.
* ``invocation_count`` — raw ``send`` calls. Under crash-retry this can exceed
  ``effects``; the provider deduped the extra calls.
"""

from __future__ import annotations


class ConnectorCrash(RuntimeError):
    """Simulates a process crash / commit failure AFTER the external effect."""


class MockConnector:
    def __init__(self, *, crash_on_first: bool = False, poison_keys: frozenset[str] | set[str] = frozenset()) -> None:
        self.invocations: list[str] = []      # raw send() calls (may exceed effects)
        self._effects: dict[str, str] = {}    # key -> provider_id (deduped effect)
        self._crash_on_first = crash_on_first
        self._crashed: set[str] = set()
        self._poison = set(poison_keys)       # keys that always raise (no effect)

    async def send(self, key: str, channel: str, payload: dict) -> str:
        self.invocations.append(key)

        # A poison key fails before any effect — models a genuinely broken send.
        if key in self._poison:
            raise ConnectorCrash(f"poison send for {key!r}")

        first_time = key not in self._effects
        if first_time:
            # The external effect happens here, once, keyed by idempotency key.
            self._effects[key] = f"prov-{len(self._effects) + 1}"
        provider_id = self._effects[key]

        # Model a crash AFTER the effect is externally durable but BEFORE our
        # bookkeeping commits: the post went out, then the process died.
        if self._crash_on_first and first_time and key not in self._crashed:
            self._crashed.add(key)
            raise ConnectorCrash(f"simulated crash after effect for {key!r}")

        return provider_id

    @property
    def effects(self) -> int:
        return len(self._effects)

    @property
    def call_count(self) -> int:
        """Distinct effects — the exactly-once metric."""
        return len(self._effects)

    @property
    def invocation_count(self) -> int:
        return len(self.invocations)
