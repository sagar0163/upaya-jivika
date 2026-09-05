"""Integration tests for SurvivalLoop.earning_cycle (wiring TaskExecutor into main.py)."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.persistence import InMemoryStore
from src.task_scorer import PaymentMethod, TaskCandidate, TaskResult, TaskType
from src.task_scorer import Platform as EarningPlatform
from src.vault import create_vault
from src.wallet import Wallet


def _loop_with_store():
    import main as main_mod

    store = InMemoryStore()
    loop = main_mod.SurvivalLoop(persistence=store)
    return loop, store


class TestEarningCycle:
    @pytest.mark.asyncio
    async def test_dead_agent_does_not_run_cycle(self):
        loop, _store = _loop_with_store()
        loop.debt_engine.state.alive = False
        loop.task_executor.run_earning_cycle = AsyncMock(return_value=[])

        results = await loop.earning_cycle()

        assert results == []
        loop.task_executor.run_earning_cycle.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_credentials_configured_skips_without_starting_browser(self):
        """No platform credentials -> skip entirely, never launch Chromium."""
        loop, _store = _loop_with_store()
        loop.task_executor.start = AsyncMock()
        loop.task_executor.run_earning_cycle = AsyncMock(return_value=[])

        results = await loop.earning_cycle()

        assert results == []
        loop.task_executor.start.assert_not_called()
        loop.task_executor.run_earning_cycle.assert_not_called()

    @pytest.mark.asyncio
    async def test_configured_credentials_but_no_tasks_is_a_safe_noop(self):
        loop, _store = _loop_with_store()
        loop.task_executor._vault = create_vault()
        loop.task_executor._vault.set_override("clickworker", "secret")
        loop.task_executor.start = AsyncMock()
        loop.task_executor.run_earning_cycle = AsyncMock(return_value=[])

        results = await loop.earning_cycle()

        assert results == []
        loop.task_executor.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_task_credits_wallet_and_records_outcome(self):
        loop, _store = _loop_with_store()
        loop.wallet = Wallet(free=Decimal("0"))
        loop.task_executor._vault = create_vault()
        loop.task_executor._vault.set_override("clickworker", "secret")
        loop.task_executor.start = AsyncMock()

        candidate = TaskCandidate(
            platform=EarningPlatform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test task",
            estimated_pay=Decimal("5.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )
        result = TaskResult(
            task_id="abc123",
            candidate=candidate,
            success=True,
            amount_earned=Decimal("5.00"),
            time_spent_hours=Decimal("1.0"),
        )
        loop.task_executor.run_earning_cycle = AsyncMock(return_value=[result])

        results = await loop.earning_cycle()

        assert len(results) == 1
        assert results[0]["amount_earned"] == "5.00"
        assert len(loop.respawn) == 1

    def test_earning_cycle_keeps_wallet_in_sync_with_task_executor(self):
        loop, _store = _loop_with_store()
        loop.wallet = Wallet(free=Decimal("42.00"))
        loop.task_executor._vault = create_vault()
        loop.task_executor._vault.set_override("clickworker", "secret")

        assert loop.task_executor.wallet is not loop.wallet

        loop.task_executor.start = AsyncMock()
        loop.task_executor.run_earning_cycle = AsyncMock(return_value=[])
        asyncio.run(loop.earning_cycle())

        assert loop.task_executor.wallet is loop.wallet
