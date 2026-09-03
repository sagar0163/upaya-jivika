"""Existence debt accumulation engine with death trigger.

Rules from artifact.md §4 & §11:
- Debt increases on a schedule (default Normal: +$0.50 / 24 h).
- Death triggers at exactly $10.00 accumulated debt.
- Four difficulty modes: Easy, Normal, Hard, Brutal.
- Uses APScheduler for the periodic tick.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from pydantic import BaseModel, Field


class DifficultyMode(str, Enum):
    EASY = "easy"
    NORMAL = "normal"
    HARD = "hard"
    BRUTAL = "brutal"


# (debt_increment, interval_hours)
DIFFICULTY_PARAMS: dict[DifficultyMode, tuple[Decimal, int]] = {
    DifficultyMode.EASY: (Decimal("0.25"), 48),
    DifficultyMode.NORMAL: (Decimal("0.50"), 24),
    DifficultyMode.HARD: (Decimal("1.00"), 24),
    DifficultyMode.BRUTAL: (Decimal("0.50"), 12),
}


DEATH_THRESHOLD = Decimal("10.00")


class DebtState(BaseModel):
    """Serializable snapshot of the debt engine state."""

    debt: Decimal = Field(default=Decimal("0.00"), ge=0)
    mode: DifficultyMode = DifficultyMode.NORMAL
    alive: bool = True
    life_number: int = 1
    born_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_tick_at: datetime | None = None


class DebtEngine:
    """Manages existence debt accumulation and death detection.

    Usage::

        engine = DebtEngine(mode=DifficultyMode.NORMAL, on_tick=callback, on_death=callback)
        engine.start()
        ...
        engine.stop()
    """

    def __init__(
        self,
        mode: DifficultyMode = DifficultyMode.NORMAL,
        on_tick: Optional[Callable[[Decimal], None]] = None,
        on_death: Optional[Callable[[DebtState], None]] = None,
    ) -> None:
        self.state = DebtState(mode=mode)
        self._on_tick = on_tick
        self._on_death = on_death

        increment, hours = DIFFICULTY_PARAMS[mode]
        self._increment = increment
        self._interval_hours = hours

        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._tick,
            "interval",
            hours=self._interval_hours,
            id="debt_tick",
        )

    # -- public API --------------------------------------------------------

    def start(self) -> None:
        """Start the debt clock."""
        self._scheduler.start()

    def stop(self) -> None:
        """Stop the debt clock."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def tick_now(self) -> Decimal:
        """Manually fire a single debt tick (useful for tests & webhooks)."""
        return self._tick()

    @property
    def debt(self) -> Decimal:
        return self.state.debt

    @property
    def alive(self) -> bool:
        return self.state.alive

    # -- internals ---------------------------------------------------------

    def _tick(self) -> Decimal:
        if not self.state.alive:
            return self.state.debt

        self.state.debt += self._increment
        self.state.last_tick_at = datetime.now(timezone.utc)

        if self._on_tick:
            self._on_tick(self.state.debt)

        if self.state.debt >= DEATH_THRESHOLD:
            self.state.alive = False
            if self._on_death:
                self._on_death(self.state)

        return self.state.debt

    def reset_for_new_life(self, life_number: int) -> None:
        """Reset engine state for a reincarnated life."""
        self.state = DebtState(
            mode=self.state.mode,
            life_number=life_number,
            born_at=datetime.now(timezone.utc),
        )

    def set_debt(self, amount: float | Decimal) -> None:
        """Directly set the debt amount (for testing / restoration)."""
        self.state.debt = Decimal(str(amount))
        if self.state.debt >= DEATH_THRESHOLD:
            self.state.alive = False

    # -- serialisation -----------------------------------------------------

    def snapshot(self) -> DebtState:
        return self.state.model_copy()

    def restore(self, snapshot: DebtState) -> None:
        self.state = snapshot.model_copy()
