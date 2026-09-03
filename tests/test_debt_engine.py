"""Unit tests for debt_engine.py — existence debt accumulation and death trigger."""

from decimal import Decimal

import pytest

from src.debt_engine import (
    DEATH_THRESHOLD,
    DebtEngine,
    DifficultyMode,
)


class TestDebtAccumulation:
    """Each tick should add the correct increment for the mode."""

    @pytest.mark.parametrize(
        "mode,expected_increment,expected_hours",
        [
            (DifficultyMode.EASY, Decimal("0.25"), 48),
            (DifficultyMode.NORMAL, Decimal("0.50"), 24),
            (DifficultyMode.HARD, Decimal("1.00"), 24),
            (DifficultyMode.BRUTAL, Decimal("0.50"), 12),
        ],
    )
    def test_tick_increments(self, mode, expected_increment, expected_hours):
        engine = DebtEngine(mode=mode)
        initial = engine.debt
        engine.tick_now()
        assert engine.debt == initial + expected_increment

    def test_multiple_ticks_accumulate(self):
        engine = DebtEngine(mode=DifficultyMode.NORMAL)
        for _ in range(4):
            engine.tick_now()
        assert engine.debt == Decimal("2.00")  # 4 × $0.50

    def test_tick_callback_fires(self):
        fired = []
        engine = DebtEngine(
            mode=DifficultyMode.NORMAL,
            on_tick=lambda d: fired.append(d),
        )
        engine.tick_now()
        assert len(fired) == 1
        assert fired[0] == Decimal("0.50")


class TestDeathTrigger:
    """Death must trigger at exactly $10.00 accumulated debt."""

    def test_death_at_10(self):
        engine = DebtEngine(mode=DifficultyMode.NORMAL)
        # 20 ticks × $0.50 = $10.00
        for _ in range(19):
            engine.tick_now()
        assert engine.alive is True
        assert engine.debt == Decimal("9.50")
        engine.tick_now()  # 20th tick → $10.00
        assert engine.debt == Decimal("10.00")
        assert engine.alive is False

    def test_no_more_ticks_after_death(self):
        engine = DebtEngine(mode=DifficultyMode.NORMAL)
        for _ in range(21):
            engine.tick_now()
        assert engine.debt == Decimal("10.00")  # capped, no further increase

    def test_death_callback_fires(self):
        deaths = []
        engine = DebtEngine(
            mode=DifficultyMode.NORMAL,
            on_death=lambda s: deaths.append(s),
        )
        for _ in range(20):
            engine.tick_now()
        assert len(deaths) == 1
        assert deaths[0].debt >= DEATH_THRESHOLD

    def test_set_debt_triggers_death(self):
        engine = DebtEngine(mode=DifficultyMode.NORMAL)
        engine.set_debt(Decimal("10.00"))
        assert engine.alive is False

    @pytest.mark.parametrize("mode,ticks_to_death", [
        (DifficultyMode.EASY, 40),     # 40 × $0.25 = $10.00
        (DifficultyMode.NORMAL, 20),   # 20 × $0.50 = $10.00
        (DifficultyMode.HARD, 10),     # 10 × $1.00 = $10.00
        (DifficultyMode.BRUTAL, 20),   # 20 × $0.50 = $10.00
    ])
    def test_all_modes_die_at_10(self, mode, ticks_to_death):
        engine = DebtEngine(mode=mode)
        for _ in range(ticks_to_death - 1):
            engine.tick_now()
        assert engine.alive is True
        engine.tick_now()
        assert engine.debt == Decimal("10.00")
        assert engine.alive is False


class TestResetForNewLife:
    """Reincarnation should reset the debt to zero with a new life number."""

    def test_reset_clears_debt(self):
        engine = DebtEngine(mode=DifficultyMode.NORMAL)
        for _ in range(20):
            engine.tick_now()
        assert engine.alive is False
        engine.reset_for_new_life(life_number=2)
        assert engine.debt == Decimal("0.00")
        assert engine.alive is True
        assert engine.state.life_number == 2

    def test_mode_preserved_after_reset(self):
        engine = DebtEngine(mode=DifficultyMode.HARD)
        engine.reset_for_new_life(life_number=2)
        assert engine.state.mode == DifficultyMode.HARD


class TestSnapshotRestore:
    def test_roundtrip(self):
        engine = DebtEngine(mode=DifficultyMode.BRUTAL)
        for _ in range(5):
            engine.tick_now()
        snap = engine.snapshot()
        engine2 = DebtEngine(mode=DifficultyMode.NORMAL)
        engine2.restore(snap)
        assert engine2.debt == engine.debt
        assert engine2.state.mode == DifficultyMode.BRUTAL
