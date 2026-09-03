"""Survival state machine driven by current debt level.

States from artifact.md §5:

    Thriving   $0 – $2.00      risk 0.7
    Surviving  $2.00 – $5.00   risk 0.5
    Struggling $5.00 – $7.50   risk 0.2
    Critical   $7.50 – $9.50   risk 0.0
    Terminal   $9.50 – $10.00  risk 0.0
    Dead       >= $10.00       —
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class State(str, Enum):
    THRIVING = "thriving"
    SURVIVING = "surviving"
    STRUGGLING = "struggling"
    CRITICAL = "critical"
    TERMINAL = "terminal"
    DEAD = "dead"


# (min_debt, max_debt, risk_tolerance)
_STATE_RANGES: list[tuple[Decimal, Decimal, Decimal, State]] = [
    (Decimal("0.00"), Decimal("2.00"), Decimal("0.70"), State.THRIVING),
    (Decimal("2.00"), Decimal("5.00"), Decimal("0.50"), State.SURVIVING),
    (Decimal("5.00"), Decimal("7.50"), Decimal("0.20"), State.STRUGGLING),
    (Decimal("7.50"), Decimal("9.50"), Decimal("0.00"), State.CRITICAL),
    (Decimal("9.50"), Decimal("10.00"), Decimal("0.00"), State.TERMINAL),
    (Decimal("10.00"), Decimal("999999"), Decimal("0.00"), State.DEAD),
]


def resolve_state(debt: Decimal) -> State:
    """Return the survival state for a given debt level."""
    for min_d, max_d, _, state in _STATE_RANGES:
        if min_d <= debt < max_d:
            return state
    # Exactly $10+ is dead (handled by last range)
    return State.DEAD


def risk_tolerance(debt: Decimal) -> Decimal:
    """Return the risk tolerance for a given debt level."""
    state = resolve_state(debt)
    for _, _, risk, s in _STATE_RANGES:
        if s == state:
            return risk
    return Decimal("0.00")


# Certainty gates per state (§6)
_CERTAINTY_GATES: dict[State, Decimal] = {
    State.THRIVING: Decimal("0.85"),
    State.SURVIVING: Decimal("0.85"),
    State.STRUGGLING: Decimal("0.70"),
    State.CRITICAL: Decimal("0.60"),
    State.TERMINAL: Decimal("0.50"),
    State.DEAD: Decimal("1.00"),  # can't do anything when dead
}


def min_certainty(debt: Decimal) -> Decimal:
    """Minimum task certainty threshold for the current debt level."""
    state = resolve_state(debt)
    return _CERTAINTY_GATES[state]


class StateTransition(BaseModel):
    """Record of a state change."""

    previous: State
    current: State
    debt: Decimal


class SurvivalStateMachine:
    """Tracks current state and fires callbacks on transitions."""

    def __init__(
        self,
        on_transition: callable = None,
    ) -> None:
        self._current_state: State = State.THRIVING
        self._on_transition = on_transition

    @property
    def state(self) -> State:
        return self._current_state

    def update(self, debt: Decimal) -> StateTransition | None:
        """Recalculate state from debt. Returns transition if state changed."""
        new_state = resolve_state(debt)
        if new_state == self._current_state:
            return None

        transition = StateTransition(
            previous=self._current_state,
            current=new_state,
            debt=debt,
        )
        self._current_state = new_state

        if self._on_transition:
            self._on_transition(transition)

        return transition

    def risk_tolerance(self) -> Decimal:
        return risk_tolerance(self.debt_value)

    @property
    def debt_value(self) -> Decimal:
        """Returns current debt as tracked internally (caller must keep in sync)."""
        # We don't store debt here; caller should pass debt to update().
        # This is a convenience that falls back to state range midpoints.
        for min_d, max_d, _, state in _STATE_RANGES:
            if state == self._current_state:
                return (min_d + max_d) / 2
        return Decimal("0.00")

    def min_certainty(self) -> Decimal:
        return _CERTAINTY_GATES[self._current_state]

    def is_alive(self) -> bool:
        return self._current_state != State.DEAD

    def reset(self) -> None:
        self._current_state = State.THRIVING
