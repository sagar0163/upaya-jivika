"""Integration test: wallet + debt engine + state machine working together."""

from decimal import Decimal

import pytest

from src.debt_engine import DebtEngine, DifficultyMode
from src.soul_crystal import ReincarnationEngine
from src.state_machine import State, SurvivalStateMachine
from src.wallet import SpendRequest, Wallet


class TestEndToEndLifecycle:
    """Simulate a full life cycle: earn, spend, accumulate debt, die, reincarnate."""

    def test_full_lifecycle(self):
        wallet = Wallet()
        engine = DebtEngine(mode=DifficultyMode.NORMAL)
        sm = SurvivalStateMachine()
        reincarnation = ReincarnationEngine()

        reincarnation.start_new_life(1)

        # Day 1-4: thriving, earning and spending
        wallet.credit_earned(Decimal("3.00"))
        sm.update(wallet.debt)
        assert sm.state == State.THRIVING

        # Day 5-10: debt accumulates, surviving
        for _ in range(8):
            engine.tick_now()
        wallet.debt = engine.debt  # sync
        sm.update(wallet.debt)
        assert sm.state == State.SURVIVING

        # Day 11-16: struggling
        for _ in range(6):
            engine.tick_now()
        wallet.debt = engine.debt
        sm.update(wallet.debt)
        assert sm.state == State.STRUGGLING

        # AI can't spend when debt > $5
        wallet.free = Decimal("50.00")
        req = SpendRequest(amount=Decimal("5.00"), certainty=Decimal("0.96"))
        with pytest.raises(Exception):
            wallet.ai_spend(req)

        # Continue to death
        for _ in range(8):
            engine.tick_now()
        wallet.debt = engine.debt
        assert engine.alive is False

        # Reincarnate
        crystal = reincarnation.on_death(wallet.debt)
        assert crystal.life == 1

        new_life = reincarnation.start_new_life(2)
        engine.reset_for_new_life(2)
        sm.reset()
        wallet = Wallet()

        assert engine.debt == Decimal("0.00")
        assert sm.state == State.THRIVING
        assert new_life.life_number == 2

        # Ancestral memory exists
        mem = reincarnation.get_ancestral_memory()
        assert "Life 1" in mem
