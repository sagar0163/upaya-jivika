"""Issue #62: the full Clickworker chain, end to end, on recorded fixtures.

discover (jobs HTML) -> score (real TaskScorer) -> execute (task-form HTML)
-> wallet credit + §20 payment window -> Payoneer completed webhook
(idempotent) -> Payoneer withdrawal.

Every browser hop runs against the recorded Clickworker HTML in
tests/form_fixture.py via a fake Playwright page — no live scraping, no real
Chromium (artifact.md §9), so the whole chain is unit-testable.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main as main_mod
from src.payoneer_webhook import PaymentStatus, PayoneerWebhookEvent
from src.persistence import InMemoryStore
from src.scam_detection import ScamTracker
from src.task_scorer import PaymentMethod, TaskCandidate, TaskScorer, TaskType
from src.task_scorer import Platform as EarningPlatform
from src.withdrawal import WithdrawalPool


def _fixture_context(html: str, screens=None):
    """A MagicMock context whose new_page() returns a FakePage of recorded HTML."""
    from tests.form_fixture import build_page

    context = MagicMock()
    context.new_page = AsyncMock(return_value=build_page(html, screens=screens))
    return context


lxml_only = pytest.mark.skipif(
    not __import__("tests.form_fixture", fromlist=["lxml_available"]).lxml_available(),
    reason="lxml required for recorded-HTML fixtures",
)


@lxml_only
class TestClickworkerWorkChain:
    """Discovery + scoring + execution of Clickworker tasks on real forms."""

    @pytest.mark.asyncio
    async def test_discover_then_score_selects_clickworker_task(self):
        """Jobs fixture scrapes into candidates; the real scorer picks one."""
        from src.task_executor import ClickworkerConnector
        from tests.form_fixture import CLICKWORKER_JOBS_HTML, instant_pacing

        connector = ClickworkerConnector(EarningPlatform.CLICKWORKER, _fixture_context(CLICKWORKER_JOBS_HTML))
        with instant_pacing():
            candidates = await connector.find_tasks()

        assert len(candidates) == 3
        assert [c.title for c in candidates] == [
            "Product Description Writing",
            "Factuality Assessment",
            "No reward shown",
        ]

        scored = TaskScorer().filter_executable(candidates, Decimal("0"))
        assert scored, "at least one discovered Clickworker task must pass the threshold"
        top = scored[0].candidate
        assert top.platform is EarningPlatform.CLICKWORKER
        assert top.payment_method is PaymentMethod.PAYONEER
        assert top.estimated_pay > 0

    @pytest.mark.asyncio
    async def test_execute_credits_wallet_and_opens_payment_window(self):
        """A completed task credits the wallet and registers a §20 window."""
        from src.task_executor import ClickworkerConnector, TaskExecutor
        from tests.form_fixture import (
            CLICKWORKER_TASK_FORM_HTML,
            CLICKWORKER_TASK_SUCCESS_HTML,
            instant_pacing,
        )

        store = InMemoryStore()
        scam_tracker = ScamTracker(store)

        with patch("src.task_executor.BrowserSessionManager") as manager_cls:
            manager = AsyncMock()
            manager.start = AsyncMock()
            manager.stop = AsyncMock()
            manager_cls.return_value = manager
            executor = TaskExecutor(wallet=None, headless=True, scam_tracker=scam_tracker)
            executor.session_manager = manager
            executor._running = True

        from src.wallet import Wallet

        executor.wallet = Wallet(free=Decimal("0"), debt=Decimal("0"))

        connector = ClickworkerConnector(
            EarningPlatform.CLICKWORKER,
            _fixture_context(
                CLICKWORKER_TASK_FORM_HTML,
                screens={"success": CLICKWORKER_TASK_SUCCESS_HTML},
            ),
        )
        executor._connectors[EarningPlatform.CLICKWORKER] = connector

        candidate = TaskCandidate(
            platform=EarningPlatform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Factuality Assessment",
            description="Evaluate whether short statements are factually correct.",
            estimated_pay=Decimal("5.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.75"),
            source_url="https://www.clickworker.com/jobs/9876",
        )

        with instant_pacing():
            result = await executor.execute_task(candidate, certainty=Decimal("0.5"))

        assert result.success is True
        assert result.amount_earned == Decimal("5.00")
        assert result.platform_data["form_submission"]
        assert executor.wallet.free == Decimal("5.00")  # no debt yet
        assert scam_tracker.unpaid_platforms() == {"clickworker"}

    @pytest.mark.asyncio
    async def test_full_chain_earning_to_payoneer_payout(self, monkeypatch):
        """Execute -> credit -> webhook confirmed -> withdrawal, all wired."""
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        from src.task_executor import ClickworkerConnector, TaskExecutor
        from tests.form_fixture import (
            CLICKWORKER_TASK_FORM_HTML,
            CLICKWORKER_TASK_SUCCESS_HTML,
            instant_pacing,
        )

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        loop.wallet.debt = Decimal("0")
        loop.wallet.free = Decimal("0")
        wallet = loop.wallet

        with patch("src.task_executor.BrowserSessionManager") as manager_cls:
            manager = AsyncMock()
            manager.start = AsyncMock()
            manager.stop = AsyncMock()
            manager_cls.return_value = manager
            executor = TaskExecutor(wallet=wallet, headless=True, scam_tracker=loop.scam_tracker)
            executor.session_manager = manager
            executor._running = True

        connector = ClickworkerConnector(
            EarningPlatform.CLICKWORKER,
            _fixture_context(
                CLICKWORKER_TASK_FORM_HTML,
                screens={"success": CLICKWORKER_TASK_SUCCESS_HTML},
            ),
        )
        executor._connectors[EarningPlatform.CLICKWORKER] = connector

        candidate = TaskCandidate(
            platform=EarningPlatform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Factuality Assessment",
            estimated_pay=Decimal("5.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.75"),
            source_url="https://www.clickworker.com/jobs/9876",
        )

        with instant_pacing():
            result = await executor.execute_task(candidate, certainty=Decimal("0.5"))

        assert result.success is True
        assert wallet.free == Decimal("5.00")

        # Payoneer webhook confirms the payout for this task's payment window.
        event = PayoneerWebhookEvent(
            payment_id=result.task_id,
            amount=result.amount_earned,
            currency="USD",
            status=PaymentStatus.COMPLETED,
        )
        first = loop.record_payment(event)
        assert first["processed"] is True
        repeat = loop.record_payment(event)
        assert repeat["processed"] is False  # idempotent on redelivery
        assert wallet.free == Decimal("10.00")

        # Once the credited balance clears, withdraw it to Payoneer.
        outcome = loop.process_withdrawal(WithdrawalPool.FREE, Decimal("5.00"))
        assert outcome["pool"] == "free"
        assert outcome["amount"] == "5.00"
        assert wallet.free == Decimal("5.00")
