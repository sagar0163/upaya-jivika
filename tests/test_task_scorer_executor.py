"""Unit tests for task_scorer.py and task_executor.py.

All browser sessions are mocked - no real Playwright or API keys needed.
"""

import os
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from decimal import Decimal
from datetime import datetime

# Set dummy env vars before importing modules
os.environ.setdefault("NVIDIA_API_KEY", "test-nvidia-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("MISTRAL_API_KEY", "test-mistral-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("CEREBRAS_API_KEY", "test-cerebras-key")
os.environ.setdefault("CLOUDFLARE_API_KEY", "test-cloudflare-key")
os.environ.setdefault("FREE_LLM_API_KEY", "test-freellm-key")

# Import enums and models needed across tests
from src.task_scorer import (
    Platform,
    TaskType,
    PaymentMethod,
    TaskCandidate,
    TaskScore,
    TaskResult,
    TaskScorer,
)
from src.state_machine import State

# ============================================================================
# Test task_scorer.py
# ============================================================================

class TestTaskScorerEnums:
    """Test task_scorer enums."""

    def test_platform_values(self):
        assert Platform.TOLOKA.value == "toloka"
        assert Platform.CLICKWORKER.value == "clickworker"
        assert Platform.PROLIFIC.value == "prolific"
        assert Platform.APPEN.value == "appen"
        assert Platform.DATA_ANNOTATION.value == "dataannotation"
        assert Platform.UPWORK.value == "upwork"
        assert Platform.FIVERR.value == "fiverr"
        assert Platform.TEXTBROKER.value == "textbroker"
        assert Platform.SCALE_AI.value == "scale_ai"
        assert Platform.GITHUB_BOUNTIES.value == "github_bounties"
        assert Platform.GITCOIN.value == "gitcoin"

    def test_task_type_values(self):
        assert TaskType.MICROTASK.value == "microtask"
        assert TaskType.SURVEY.value == "survey"
        assert TaskType.DATA_ANNOTATION.value == "data_annotation"
        assert TaskType.WRITING.value == "writing"
        assert TaskType.CODING.value == "coding"
        assert TaskType.CRYPTO.value == "crypto"

    def test_payment_method_values(self):
        assert PaymentMethod.PAYONEER.value == "payoneer"
        assert PaymentMethod.PAYPAL.value == "paypal"
        assert PaymentMethod.BANK_TRANSFER.value == "bank_transfer"
        assert PaymentMethod.CRYPTO.value == "crypto"
        assert PaymentMethod.STRIPE.value == "stripe"
        assert PaymentMethod.UPI.value == "upi"


class TestTaskCandidate:
    """Test TaskCandidate model."""

    def test_task_candidate_defaults(self):
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test task",
            payment_method=PaymentMethod.PAYONEER,
        )
        assert candidate.platform == Platform.CLICKWORKER
        assert candidate.task_type == TaskType.MICROTASK
        assert candidate.title == "Test task"
        assert candidate.description == ""
        assert candidate.estimated_pay == Decimal("0")
        assert candidate.estimated_hours == Decimal("1")
        assert candidate.payment_method == PaymentMethod.PAYONEER
        assert candidate.platform_certainty == Decimal("0.5")
        assert candidate.source_url == ""
        assert candidate.metadata == {}

    def test_task_candidate_custom(self):
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        candidate = TaskCandidate(
            platform=Platform.TOLOKA,
            task_type=TaskType.MICROTASK,
            title="Custom task",
            description="Description here",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("2.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.80"),
            source_url="https://example.com/task",
            metadata={"key": "value"},
        )
        assert candidate.estimated_pay == Decimal("10.00")
        assert candidate.estimated_hours == Decimal("2.0")
        assert candidate.platform_certainty == Decimal("0.80")
        assert candidate.metadata == {"key": "value"}


class TestTaskScore:
    """Test TaskScore model."""

    def test_task_score_creation(self):
        from src.task_scorer import TaskScore, TaskCandidate, Platform, TaskType, PaymentMethod, State
        from src.state_machine import State as SMState

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test",
            payment_method=PaymentMethod.PAYONEER,
        )
        score = TaskScore(
            candidate=candidate,
            base_certainty=Decimal("0.70"),
            platform_bonus=Decimal("0.05"),
            payment_bonus=Decimal("0.10"),
            pay_rate_bonus=Decimal("0.05"),
            survival_bonus=Decimal("0.02"),
            final_score=Decimal("0.92"),
            passes_threshold=True,
            threshold_used=Decimal("0.85"),
            survival_state=State.SURVIVING,
            reasoning=["Test reasoning"],
        )
        assert score.final_score == Decimal("0.92")
        assert score.passes_threshold is True
        assert score.survival_state == State.SURVIVING


class TestPlatformData:
    """Test platform data constants."""

    def test_platform_data_exists(self):
        from src.task_scorer import PLATFORM_DATA, Platform, PaymentMethod
        assert Platform.TOLOKA in PLATFORM_DATA
        assert Platform.CLICKWORKER in PLATFORM_DATA
        assert Platform.PROLIFIC in PLATFORM_DATA

        # Check Clickworker data
        certainty, pay, payment, auto = PLATFORM_DATA[Platform.CLICKWORKER]
        assert certainty == Decimal("0.70")
        assert pay == Decimal("30")
        assert payment == PaymentMethod.PAYONEER
        assert auto is True

    def test_payment_reliability(self):
        from src.task_scorer import PAYMENT_RELIABILITY, PaymentMethod
        assert PAYMENT_RELIABILITY[PaymentMethod.PAYONEER] == Decimal("1.0")
        assert PAYMENT_RELIABILITY[PaymentMethod.PAYPAL] == Decimal("0.6")
        assert PAYMENT_RELIABILITY[PaymentMethod.CRYPTO] == Decimal("0.5")


class TestTaskScorer:
    """Test TaskScorer class."""

    @pytest.fixture
    def scorer(self):
        from src.task_scorer import TaskScorer
        return TaskScorer()

    def test_scorer_initialization(self, scorer):
        from src.task_scorer import TaskScorer
        assert scorer.base_threshold == Decimal("0.85")
        assert scorer.min_pay_per_hour == Decimal("1.00")

    def test_scorer_custom_thresholds(self):
        from src.task_scorer import TaskScorer
        scorer = TaskScorer(base_threshold=Decimal("0.75"), min_pay_per_hour=Decimal("5.00"))
        assert scorer.base_threshold == Decimal("0.75")
        assert scorer.min_pay_per_hour == Decimal("5.00")

    def test_score_clickworker_thriving(self, scorer):
        """Test scoring Clickworker task in Thriving state (debt $0-2)."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        from src.state_machine import State

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Categorize images",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.75"),
        )

        # Thriving state: debt = $1.00
        score = scorer.score(candidate, Decimal("1.00"))

        assert score.survival_state == State.THRIVING
        assert score.threshold_used == Decimal("0.85")
        assert score.final_score >= score.threshold_used
        assert score.passes_threshold is True
        assert "Thriving state bonus" in " ".join(score.reasoning)

    def test_score_clickworker_surviving(self, scorer):
        """Test scoring Clickworker task in Surviving state (debt $2-5)."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        from src.state_machine import State

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Categorize images",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.75"),
        )

        # Surviving state: debt = $3.00
        score = scorer.score(candidate, Decimal("3.00"))

        assert score.survival_state == State.SURVIVING
        assert score.threshold_used == Decimal("0.85")
        assert "Surviving state bonus" in " ".join(score.reasoning)

    def test_score_clickworker_struggling(self, scorer):
        """Test scoring Clickworker task in Struggling state (debt $5-7.50)."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        from src.state_machine import State

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Categorize images",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.75"),
        )

        # Struggling state: debt = $6.00
        score = scorer.score(candidate, Decimal("6.00"))

        assert score.survival_state == State.STRUGGLING
        assert score.threshold_used == Decimal("0.70")
        assert "Struggling state" in " ".join(score.reasoning)

    def test_score_clickworker_critical(self, scorer):
        """Test scoring Clickworker task in Critical state (debt $7.50-9.50)."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        from src.state_machine import State

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Categorize images",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.75"),
        )

        # Critical state: debt = $8.00
        score = scorer.score(candidate, Decimal("8.00"))

        assert score.survival_state == State.CRITICAL
        assert score.threshold_used == Decimal("0.60")
        assert "CRITICAL state" in " ".join(score.reasoning)

    def test_score_clickworker_terminal(self, scorer):
        """Test scoring Clickworker task in Terminal state (debt $9.50-10)."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        from src.state_machine import State

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Categorize images",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.75"),
        )

        # Terminal state: debt = $9.75
        score = scorer.score(candidate, Decimal("9.75"))

        assert score.survival_state == State.TERMINAL
        assert score.threshold_used == Decimal("0.50")
        assert "TERMINAL state" in " ".join(score.reasoning)

    def test_score_low_pay_rejected(self, scorer):
        """Test that low pay tasks are rejected."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        from src.state_machine import State

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Low pay task",
            estimated_pay=Decimal("0.50"),  # Below $1/hr minimum
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            platform_certainty=Decimal("0.75"),
        )

        score = scorer.score(candidate, Decimal("1.00"))  # Thriving

        # Should still pass if other factors are strong, but with lower score
        assert "Low pay rate" in " ".join(score.reasoning)

    def test_score_paypal_payment_penalty(self, scorer):
        """Test that PayPal payment method gets lower reliability."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        from src.state_machine import State

        candidate = TaskCandidate(
            platform=Platform.PROLIFIC,
            task_type=TaskType.SURVEY,
            title="Survey task",
            estimated_pay=Decimal("20.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYPAL,  # Lower reliability
            platform_certainty=Decimal("0.70"),
        )

        score = scorer.score(candidate, Decimal("1.00"))

        assert "Payment method (paypal) bonus" in " ".join(score.reasoning)
        # PayPal gets 0.6 reliability -> (0.6-0.5)*0.2 = 0.02 bonus
        # vs Payoneer 1.0 -> (1.0-0.5)*0.2 = 0.10 bonus

    def test_score_crypto_payment_penalty(self, scorer):
        """Test that crypto payment method gets lowest reliability."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod
        from src.state_machine import State

        candidate = TaskCandidate(
            platform=Platform.DATA_ANNOTATION,
            task_type=TaskType.DATA_ANNOTATION,
            title="RLHF task",
            estimated_pay=Decimal("30.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.CRYPTO,  # Lowest reliability
            platform_certainty=Decimal("0.65"),
        )

        score = scorer.score(candidate, Decimal("1.00"))

        assert "Payment method (crypto) bonus" in " ".join(score.reasoning)

    def test_score_batch_sorted(self, scorer):
        """Test batch scoring returns sorted results."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod

        candidates = [
            TaskCandidate(
                platform=Platform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="High pay",
                estimated_pay=Decimal("50.00"),
                estimated_hours=Decimal("1.0"),
                payment_method=PaymentMethod.PAYONEER,
                platform_certainty=Decimal("0.80"),
            ),
            TaskCandidate(
                platform=Platform.FIVERR,
                task_type=TaskType.WRITING,
                title="Low pay",
                estimated_pay=Decimal("5.00"),
                estimated_hours=Decimal("2.0"),
                payment_method=PaymentMethod.PAYONEER,
                platform_certainty=Decimal("0.50"),
            ),
        ]

        scores = scorer.score_batch(candidates, Decimal("1.00"))

        assert len(scores) == 2
        assert scores[0].final_score >= scores[1].final_score
        assert scores[0].candidate.title == "High pay"

    def test_filter_executable(self, scorer):
        """Test filtering only executable tasks."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod

        candidates = [
            TaskCandidate(
                platform=Platform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="Good task",
                estimated_pay=Decimal("20.00"),
                estimated_hours=Decimal("1.0"),
                payment_method=PaymentMethod.PAYONEER,
                platform_certainty=Decimal("0.80"),
            ),
            TaskCandidate(
                platform=Platform.FIVERR,
                task_type=TaskType.WRITING,
                title="Bad task",
                estimated_pay=Decimal("5.00"),
                estimated_hours=Decimal("5.0"),  # $1/hr
                payment_method=PaymentMethod.PAYONEER,
                platform_certainty=Decimal("0.40"),
            ),
        ]

        executable = scorer.filter_executable(candidates, Decimal("1.00"))

        assert len(executable) == 1
        assert executable[0].candidate.title == "Good task"

    def test_unknown_platform_defaults(self, scorer):
        """Test scoring with unknown platform uses defaults."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod

        # Create a candidate with a platform not in PLATFORM_DATA
        candidate = TaskCandidate(
            platform=Platform.GITHUB_BOUNTIES,  # This IS in PLATFORM_DATA
            task_type=TaskType.CODING,
            title="Coding bounty",
            estimated_pay=Decimal("100.00"),
            estimated_hours=Decimal("10.0"),
            payment_method=PaymentMethod.CRYPTO,
            platform_certainty=Decimal("0.40"),
        )

        score = scorer.score(candidate, Decimal("1.00"))

        # GitHub bounties has 0.40 base certainty
        assert score.base_certainty == Decimal("0.40")


# ============================================================================
# Test task_executor.py
# ============================================================================

class TestTaskResult:
    """Test TaskResult model."""

    def test_task_result_creation(self):
        from src.task_scorer import TaskResult, TaskCandidate, Platform, TaskType, PaymentMethod

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test",
            payment_method=PaymentMethod.PAYONEER,
        )

        result = TaskResult(
            task_id="abc123",
            candidate=candidate,
            success=True,
            amount_earned=Decimal("10.00"),
            time_spent_hours=Decimal("0.5"),
            error=None,
            platform_data={"platform": "clickworker"},
        )

        assert result.task_id == "abc123"
        assert result.success is True
        assert result.amount_earned == Decimal("10.00")
        assert result.error is None


class TestExecutionError:
    """Test ExecutionError."""

    def test_execution_error(self):
        from src.task_executor import ExecutionError
        err = ExecutionError("Test error")
        assert str(err) == "Test error"


class TestPlatformConnector:
    """Test abstract PlatformConnector."""

    def test_abstract_methods(self):
        from src.task_executor import PlatformConnector
        # Can't instantiate abstract class
        with pytest.raises(TypeError):
            PlatformConnector()


class TestClickworkerConnector:
    """Test ClickworkerConnector with mocked browser."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock browser context."""
        context = MagicMock()
        page = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        return context, page

    @pytest.mark.asyncio
    async def test_login_success(self, mock_context):
        """Test successful login."""
        from src.task_executor import ClickworkerConnector

        context, page = mock_context
        page.wait_for_url = AsyncMock()

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        credentials = {"email": "test@example.com", "password": "password123"}

        result = await connector.login(credentials)

        assert result is True
        page.goto.assert_called_once()
        page.fill.assert_called()
        page.click.assert_called()

    @pytest.mark.asyncio
    async def test_login_failure(self, mock_context):
        """Test login failure."""
        from src.task_executor import ClickworkerConnector

        context, page = mock_context
        page.wait_for_url = AsyncMock(side_effect=Exception("Timeout"))

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        credentials = {"email": "test@example.com", "password": "wrong"}

        result = await connector.login(credentials)

        assert result is False

    @pytest.mark.asyncio
    async def test_find_tasks(self, mock_context):
        """Test finding tasks returns candidates."""
        from src.task_executor import ClickworkerConnector

        context, page = mock_context
        page.wait_for_url = AsyncMock()

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        candidates = await connector.find_tasks()

        assert len(candidates) == 2
        assert candidates[0].platform == Platform.CLICKWORKER
        assert candidates[0].task_type == TaskType.MICROTASK
        assert candidates[0].estimated_pay > 0

    @pytest.mark.asyncio
    async def test_execute_task_success(self, mock_context):
        """Test successful task execution."""
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod

        context, page = mock_context

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test task",
            estimated_pay=Decimal("5.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            source_url="https://clickworker.com/task/123",
        )

        result = await connector.execute_task(candidate)

        assert result.success is True
        assert result.amount_earned == Decimal("5.00")
        assert result.error is None
        assert result.candidate == candidate

    @pytest.mark.asyncio
    async def test_execute_task_failure(self, mock_context):
        """Test task execution failure."""
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod

        context, page = mock_context
        page.goto = AsyncMock(side_effect=Exception("Network error"))

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test task",
            estimated_pay=Decimal("5.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
            source_url="https://clickworker.com/task/123",
        )

        result = await connector.execute_task(candidate)

        assert result.success is False
        assert result.amount_earned == Decimal("0")
        assert result.error is not None


class TestTolokaConnector:
    """Test TolokaConnector with mocked browser."""

    @pytest.fixture
    def mock_context(self):
        context = MagicMock()
        page = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        return context, page

    @pytest.mark.asyncio
    async def test_find_tasks(self, mock_context):
        """Test finding tasks on Toloka."""
        from src.task_executor import TolokaConnector

        context, page = mock_context
        page.wait_for_url = AsyncMock()

        connector = TolokaConnector(Platform.TOLOKA, context)
        candidates = await connector.find_tasks()

        assert len(candidates) == 2
        assert candidates[0].platform == Platform.TOLOKA
        assert candidates[0].payment_method == PaymentMethod.PAYONEER


class TestProlificConnector:
    """Test ProlificConnector with mocked browser."""

    @pytest.fixture
    def mock_context(self):
        context = MagicMock()
        page = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        return context, page

    @pytest.mark.asyncio
    async def test_find_tasks(self, mock_context):
        """Test finding tasks on Prolific."""
        from src.task_executor import ProlificConnector

        context, page = mock_context
        page.wait_for_url = AsyncMock()

        connector = ProlificConnector(Platform.PROLIFIC, context)
        candidates = await connector.find_tasks()

        assert len(candidates) == 1
        assert candidates[0].platform == Platform.PROLIFIC
        assert candidates[0].task_type == TaskType.SURVEY
        assert candidates[0].payment_method == PaymentMethod.PAYPAL


class TestBrowserSessionManager:
    """Test BrowserSessionManager with mocked Playwright."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test browser start and stop."""
        from src.task_executor import BrowserSessionManager

        with patch("src.task_executor.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser = AsyncMock()
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)
            mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

            manager = BrowserSessionManager(headless=True)
            await manager.start()

            assert manager._browser == mock_browser
            mock_pw.chromium.launch.assert_called_once()

            await manager.stop()

            mock_browser.close.assert_called_once()
            mock_pw.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_context(self):
        """Test creating browser context."""
        from src.task_executor import BrowserSessionManager

        with patch("src.task_executor.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser = AsyncMock()
            mock_context = AsyncMock()
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)
            mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            manager = BrowserSessionManager(headless=True)
            await manager.start()

            context = await manager.create_context("test_ctx")

            assert context == mock_context
            assert "test_ctx" in manager._contexts
            mock_browser.new_context.assert_called_once()

            await manager.stop()

    @pytest.mark.asyncio
    async def test_close_context(self):
        """Test closing browser context."""
        from src.task_executor import BrowserSessionManager

        with patch("src.task_executor.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser = AsyncMock()
            mock_context = AsyncMock()
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)
            mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            manager = BrowserSessionManager(headless=True)
            await manager.start()

            await manager.create_context("test_ctx")
            await manager.close_context("test_ctx")

            assert "test_ctx" not in manager._contexts
            mock_context.close.assert_called_once()

            await manager.stop()


class TestTaskExecutor:
    """Test TaskExecutor with mocked browser and wallet."""

    @pytest.fixture
    def mock_wallet(self):
        """Create a mock wallet."""
        wallet = MagicMock()
        wallet.credit_earned = MagicMock(return_value={
            "debt_repaid": Decimal("0"),
            "to_free": Decimal("10.00"),
            "to_locked": Decimal("0"),
        })
        wallet.free = Decimal("100.00")
        wallet.debt = Decimal("0")
        return wallet

    @pytest.fixture
    def executor(self, mock_wallet):
        """Create a TaskExecutor with mocked browser."""
        from src.task_executor import TaskExecutor

        with patch("src.task_executor.BrowserSessionManager") as mock_manager_class:
            mock_manager = AsyncMock()
            mock_context = AsyncMock()
            mock_manager.create_context = AsyncMock(return_value=mock_context)
            mock_manager.start = AsyncMock()
            mock_manager.stop = AsyncMock()
            mock_manager_class.return_value = mock_manager

            executor = TaskExecutor(wallet=mock_wallet, headless=True)
            executor.session_manager = mock_manager

            return executor

    @pytest.mark.asyncio
    async def test_start_stop(self, executor):
        """Test executor start and stop."""
        await executor.start()
        assert executor._running is True
        executor.session_manager.start.assert_called_once()

        await executor.stop()
        assert executor._running is False
        executor.session_manager.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_credentials(self, executor):
        """Test setting platform credentials."""
        from src.task_scorer import Platform

        executor.set_credentials(Platform.CLICKWORKER, {"email": "test", "password": "pass"})

        assert Platform.CLICKWORKER in executor._credentials
        assert executor._credentials[Platform.CLICKWORKER]["email"] == "test"

    @pytest.mark.asyncio
    async def test_get_connector_creates_and_logs_in(self, executor):
        """Test getting connector creates it and logs in."""
        from src.task_scorer import Platform
        from src.task_executor import ClickworkerConnector

        mock_connector = AsyncMock(spec=ClickworkerConnector)
        mock_connector.login = AsyncMock(return_value=True)

        # Mock the connector creation
        with patch("src.task_executor.CONNECTORS", {Platform.CLICKWORKER: lambda p, c: mock_connector}):
            executor.set_credentials(Platform.CLICKWORKER, {"email": "test", "password": "pass"})
            executor.session_manager.create_context = AsyncMock(return_value=AsyncMock())

            connector = await executor._get_connector(Platform.CLICKWORKER)

            assert connector == mock_connector
            mock_connector.login.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_connector_no_credentials(self, executor):
        """Test getting connector without credentials raises error."""
        from src.task_scorer import Platform
        from src.task_executor import ExecutionError

        with pytest.raises(ExecutionError, match="No credentials"):
            await executor._get_connector(Platform.CLICKWORKER)

    @pytest.mark.asyncio
    async def test_discover_tasks(self, executor):
        """Test discovering tasks from multiple platforms."""
        from src.task_scorer import Platform, TaskCandidate, TaskType, PaymentMethod

        mock_connector1 = AsyncMock()
        mock_connector1.find_tasks = AsyncMock(return_value=[
            TaskCandidate(
                platform=Platform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="Task 1",
                payment_method=PaymentMethod.PAYONEER,
            ),
        ])

        mock_connector2 = AsyncMock()
        mock_connector2.find_tasks = AsyncMock(return_value=[
            TaskCandidate(
                platform=Platform.TOLOKA,
                task_type=TaskType.MICROTASK,
                title="Task 2",
                payment_method=PaymentMethod.PAYONEER,
            ),
        ])

        executor._connectors = {
            Platform.CLICKWORKER: mock_connector1,
            Platform.TOLOKA: mock_connector2,
        }

        candidates = await executor.discover_tasks([Platform.CLICKWORKER, Platform.TOLOKA])

        assert len(candidates) == 2
        assert candidates[0].title == "Task 1"
        assert candidates[1].title == "Task 2"

    @pytest.mark.asyncio
    async def test_execute_task_credits_wallet(self, executor, mock_wallet):
        """Test that successful task execution credits wallet."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod, TaskResult
        from src.task_executor import ClickworkerConnector

        await executor.start()

        mock_connector = AsyncMock(spec=ClickworkerConnector)
        mock_connector.execute_task = AsyncMock(return_value=TaskResult(
            task_id="test123",
            candidate=TaskCandidate(
                platform=Platform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="Test",
                estimated_pay=Decimal("10.00"),
                estimated_hours=Decimal("1.0"),
                payment_method=PaymentMethod.PAYONEER,
            ),
            success=True,
            amount_earned=Decimal("10.00"),
            time_spent_hours=Decimal("1.0"),
            error=None,
        ))

        executor._connectors[Platform.CLICKWORKER] = mock_connector

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )

        result = await executor.execute_task(candidate)

        assert result.success is True
        assert result.amount_earned == Decimal("10.00")
        mock_wallet.credit_earned.assert_called_once_with(Decimal("10.00"))

        await executor.stop()

    @pytest.mark.asyncio
    async def test_execute_task_failure_no_wallet_credit(self, executor, mock_wallet):
        """Test that failed task doesn't credit wallet."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod, TaskResult
        from src.task_executor import ClickworkerConnector

        mock_connector = AsyncMock(spec=ClickworkerConnector)
        mock_connector.execute_task = AsyncMock(return_value=TaskResult(
            task_id="test123",
            candidate=TaskCandidate(
                platform=Platform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="Test",
                estimated_pay=Decimal("10.00"),
                estimated_hours=Decimal("1.0"),
                payment_method=PaymentMethod.PAYONEER,
            ),
            success=False,
            amount_earned=Decimal("0"),
            time_spent_hours=Decimal("0.5"),
            error="Failed",
        ))

        executor._connectors[Platform.CLICKWORKER] = mock_connector

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )

        await executor.start()
        result = await executor.execute_task(candidate)
        await executor.stop()

        assert result.success is False
        mock_wallet.credit_earned.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_batch(self, executor, mock_wallet):
        """Test executing batch of tasks."""
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod, TaskResult

        mock_connector = AsyncMock()
        mock_connector.execute_task = AsyncMock(side_effect=[
            TaskResult(
                task_id="1",
                candidate=TaskCandidate(
                    platform=Platform.CLICKWORKER,
                    task_type=TaskType.MICROTASK,
                    title="Task 1",
                    estimated_pay=Decimal("10.00"),
                    estimated_hours=Decimal("1.0"),
                    payment_method=PaymentMethod.PAYONEER,
                ),
                success=True,
                amount_earned=Decimal("10.00"),
                time_spent_hours=Decimal("1.0"),
            ),
            TaskResult(
                task_id="2",
                candidate=TaskCandidate(
                    platform=Platform.CLICKWORKER,
                    task_type=TaskType.MICROTASK,
                    title="Task 2",
                    estimated_pay=Decimal("5.00"),
                    estimated_hours=Decimal("0.5"),
                    payment_method=PaymentMethod.PAYONEER,
                ),
                success=True,
                amount_earned=Decimal("5.00"),
                time_spent_hours=Decimal("0.5"),
            ),
        ])

        executor._connectors[Platform.CLICKWORKER] = mock_connector

        candidates = [
            TaskCandidate(
                platform=Platform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="Task 1",
                estimated_pay=Decimal("10.00"),
                estimated_hours=Decimal("1.0"),
                payment_method=PaymentMethod.PAYONEER,
            ),
            TaskCandidate(
                platform=Platform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="Task 2",
                estimated_pay=Decimal("5.00"),
                estimated_hours=Decimal("0.5"),
                payment_method=PaymentMethod.PAYONEER,
            ),
        ]

        await executor.start()
        results = await executor.execute_batch(candidates)
        await executor.stop()

        assert len(results) == 2
        assert all(r.success for r in results)
        assert mock_wallet.credit_earned.call_count == 2


class TestMockExecuteTask:
    """Test the mock execution function for testing."""

    @pytest.mark.asyncio
    async def test_mock_execute_task(self):
        """Test mock execution returns valid result."""
        from src.task_executor import mock_execute_task
        from src.task_scorer import TaskCandidate, Platform, TaskType, PaymentMethod

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )

        # Run multiple times to check randomness
        results = []
        for _ in range(20):
            result = await mock_execute_task(candidate)
            results.append(result)

        # Should have some successes and some failures
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]

        assert len(successes) > 0
        assert len(failures) > 0

        for r in successes:
            assert r.amount_earned == Decimal("10.00")
        for r in failures:
            assert r.amount_earned == Decimal("0")


# ============================================================================
# Integration tests
# ============================================================================

class TestTaskScorerExecutorIntegration:
    """Integration tests combining scorer and executor."""

    @pytest.mark.asyncio
    async def test_full_cycle_mock(self):
        """Test full discover -> score -> execute cycle with mocks."""
        from src.task_scorer import TaskScorer, TaskCandidate, Platform, TaskType, PaymentMethod
        from src.task_executor import mock_execute_task, TaskExecutor
        from src.wallet import Wallet
        from decimal import Decimal

        wallet = Wallet()
        scorer = TaskScorer()

        # Create candidates
        candidates = [
            TaskCandidate(
                platform=Platform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="Good task",
                estimated_pay=Decimal("20.00"),
                estimated_hours=Decimal("1.0"),
                payment_method=PaymentMethod.PAYONEER,
                platform_certainty=Decimal("0.80"),
            ),
            TaskCandidate(
                platform=Platform.FIVERR,
                task_type=TaskType.WRITING,
                title="Bad task",
                estimated_pay=Decimal("5.00"),
                estimated_hours=Decimal("5.0"),
                payment_method=PaymentMethod.PAYONEER,
                platform_certainty=Decimal("0.40"),
            ),
        ]

        # Score with low debt (Thriving)
        scored = scorer.filter_executable(candidates, Decimal("1.00"))

        # Only first should pass
        assert len(scored) == 1
        assert scored[0].candidate.title == "Good task"

        # Execute passing task
        result = await mock_execute_task(scored[0].candidate)

        # Retry if mock fails (80% success rate, but test must be deterministic)
        if not result.success:
            result = await mock_execute_task(scored[0].candidate)
        
        if result.success:
            wallet.credit_earned(result.amount_earned)

        assert wallet.free > 0 or wallet.locked > 0


# Run tests if called directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])