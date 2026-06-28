"""Capped, spaced sequence planner (bead 1mk.7) — the `outreach-sequence-builder`.

Encodes spec §5 exactly:
- **4 touches, widening gaps: day 0 / +3 / +5 / +7** (`TOUCH_DAY_OFFSETS`).
- **Warmup ramp** then steady cap (per inbox/day): ~8 (wk1) → 18 → 28 → 40 (wk4),
  full by wk5+; consumer Gmail steady-caps at 25. Hard system caps are NOT targets.
- **Hard-stop on reply / bounce / unsubscribe / spam-complaint** — the sequence
  halts immediately; no further touches are planned.
- **RFC 8058 one-click unsubscribe on every touch** (enforced by Touch default).

Pure planning: it produces the touch plan + the day's cap. It never sends and
never schedules a real action (439 hold) — the policy routes everything to review.
"""

from __future__ import annotations

from outreach.schema import (
    MAX_TOUCHES,
    TOUCH_DAY_OFFSETS,
    OutreachSequence,
    StopReason,
    Touch,
)

# Per-inbox/day warmup ramp by week (spec §5). Index 0 unused; weeks are 1-based.
_WARMUP_BY_WEEK = (0, 8, 18, 28, 40)   # wk1..wk4
_STEADY_WORKSPACE = 40                  # default steady cap (Workspace)
_STEADY_CONSUMER = 25                   # consumer Gmail steady cap

_TOUCH_PURPOSE = ("intro", "value-add", "soft-CTA", "break-up")

# Events that immediately end a sequence (spec §5 hard-stop).
_HARD_STOP: frozenset[StopReason] = frozenset({"reply", "bounce", "unsubscribe", "spam_complaint"})


def cap_per_inbox_day(week: int, *, consumer: bool = False) -> int:
    """Warmup-aware daily cap for an inbox in a given (1-based) week."""
    if week < 1:
        week = 1
    steady = _STEADY_CONSUMER if consumer else _STEADY_WORKSPACE
    if week >= len(_WARMUP_BY_WEEK):
        return steady
    ramped = _WARMUP_BY_WEEK[week]
    return min(ramped, steady)


class SequencePlanner:
    """Builds the capped 4-touch plan and applies the hard-stop rule."""

    def __init__(self, *, week: int = 5, consumer: bool = False) -> None:
        self._week = week
        self._consumer = consumer

    def stop_index(self, events: tuple[StopReason, ...]) -> int | None:
        """If any hard-stop event occurred, the sequence halts: return the touch
        count that should have been delivered before halting (>=0), else None."""
        return 0 if any(e in _HARD_STOP for e in events) else None

    def build(
        self,
        *,
        allowed_briefs: list[tuple[str, ...]] | None = None,
        events: tuple[StopReason, ...] = (),
    ) -> OutreachSequence:
        """Plan up to MAX_TOUCHES. If a hard-stop event is present, the sequence
        is empty (halted). ``allowed_briefs[i]`` is the per-touch personalization
        brief (from the over-personalization guard)."""
        if self.stop_index(events) is not None:
            return OutreachSequence(touches=(), cap_per_inbox_day=self._cap())

        touches: list[Touch] = []
        for i in range(MAX_TOUCHES):
            brief = ()
            if allowed_briefs and i < len(allowed_briefs):
                brief = allowed_briefs[i]
            touches.append(
                Touch(
                    index=i + 1,
                    day_offset=TOUCH_DAY_OFFSETS[i],
                    purpose=_TOUCH_PURPOSE[i],
                    personalization_brief=brief,
                    includes_unsubscribe=True,
                )
            )
        return OutreachSequence(touches=tuple(touches), cap_per_inbox_day=self._cap())

    def _cap(self) -> int:
        return cap_per_inbox_day(self._week, consumer=self._consumer)
