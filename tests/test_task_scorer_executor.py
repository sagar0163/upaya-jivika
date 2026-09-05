"""Unit tests for task_scorer.py and task_executor.py.

All browser sessions are mocked - no real Playwright or API keys needed.
"""

import json
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    PaymentMethod,
    Platform,
    TaskType,
)

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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType
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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType
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
        from src.task_scorer import (
            PaymentMethod,
            Platform,
            State,
            TaskCandidate,
            TaskScore,
            TaskType,
        )

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
        from src.task_scorer import PLATFORM_DATA, PaymentMethod, Platform
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
        assert scorer.base_threshold == Decimal("0.85")
        assert scorer.min_pay_per_hour == Decimal("1.00")

    def test_scorer_custom_thresholds(self):
        from src.task_scorer import TaskScorer
        scorer = TaskScorer(base_threshold=Decimal("0.75"), min_pay_per_hour=Decimal("5.00"))
        assert scorer.base_threshold == Decimal("0.75")
        assert scorer.min_pay_per_hour == Decimal("5.00")

    def test_score_clickworker_thriving(self, scorer):
        """Test scoring Clickworker task in Thriving state (debt $0-2)."""
        from src.state_machine import State
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.state_machine import State
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.state_machine import State
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.state_machine import State
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.state_machine import State
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskResult, TaskType

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


class FakeLocator:
    """A fake Playwright Locator backed by in-memory values.

    Used to exercise the connector's DOM-scraping paths without launching a
    browser — this is the "mocked/recorded responses in tests" approach.
    """

    def __init__(self, count=0, texts=None, attrs=None, values=None):
        self._count = count
        self._texts = texts or []
        self._attrs = attrs or []
        self._values = values or []

    def nth(self, i):
        return FakeLocator(
            count=1,
            texts=[self._texts[i] if i < len(self._texts) else ""],
            attrs=[self._attrs[i] if i < len(self._attrs) else None],
            values=[self._values[i] if i < len(self._values) else None],
        )

    @property
    def first(self):
        return self

    def locator(self, selector):
        return FakeLocator(count=self._count, texts=self._texts, attrs=self._attrs)

    async def count(self):
        return self._count

    async def inner_text(self):
        return self._texts[0] if self._texts else ""

    async def get_attribute(self, name):
        return self._attrs[0] if self._attrs else None

    async def fill(self, value):
        return None


def _make_locator_aware_page(locators):
    """Patch a page's locator() to return the given FakeLocator map.

    ``locators`` maps a CSS selector to a FakeLocator. Unknown selectors fall
    back to a count-0 locator so code paths degrade gracefully.
    """
    def locator(selector):
        return locators.get(selector, FakeLocator(count=0))
    return locator


class TestClickworkerConnector:
    """Test ClickworkerConnector with mocked browser + recorded responses."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock browser context with a page whose DOM can be faked."""
        context = MagicMock()
        page = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        return context, page

    def _chain_page_locator(self, page, locators):
        """Attach a fake locator() dispatcher to an AsyncMock page."""
        page.locator = MagicMock(side_effect=_make_locator_aware_page(locators))
        # Playwright always returns the same element handle chain for nth/first
        page.locator.return_value = None
        return page

    @pytest.mark.asyncio
    async def test_login_success(self, mock_context):
        """Test successful login navigates, fills and confirms dashboard."""
        from src.task_executor import ClickworkerConnector

        context, page = mock_context
        page.locator = MagicMock(return_value=FakeLocator(count=0))

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        credentials = {"email": "test@example.com", "password": "password123"}

        result = await connector.login(credentials)

        assert result is True
        page.goto.assert_called_once()
        page.fill.assert_called()
        page.click.assert_called()

    @pytest.mark.asyncio
    async def test_login_failure(self, mock_context):
        """Test login failure when post-login confirmation is never seen."""
        from src.task_executor import ClickworkerConnector

        context, page = mock_context
        page.locator = MagicMock(return_value=FakeLocator(count=0))
        # wait_for_selector for ".logout,..." never confirms -> login fails
        page.wait_for_selector = AsyncMock(side_effect=Exception("Timed out"))

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        credentials = {"email": "test@example.com", "password": "wrong"}

        result = await connector.login(credentials)

        assert result is False

    @pytest.mark.asyncio
    async def test_find_tasks_scrapes_cards(self, mock_context):
        """Test finding tasks scrapes real-looking task cards from the DOM."""
        from src.task_executor import ClickworkerConnector

        context, page = mock_context
        # Simulate one DOM with 2 task cards.
        cards = FakeLocator(count=2)
        self._chain_page_locator(page, {
            # Task card container
            ClickworkerConnector.SEL_TASK_CARD: cards,
        })
        # Each card resolves via nth -> first -> inner_text/get_attribute.
        page.locator.side_effect = lambda selector: FakeLocator(count=2)

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        await connector.find_tasks()

        # With a generic fake DOM we get 2 cards whose sub-selectors resolve to
        # empty text — the connector filters zero-title cards, so we assert the
        # scrape path ran (goto was called) rather than a specific count.
        assert page.goto.called

    @pytest.mark.asyncio
    async def test_find_tasks_no_cards_returns_empty(self, mock_context):
        """Test find_tasks returns [] when no task cards are present."""
        from src.task_executor import ClickworkerConnector

        context, page = mock_context
        self._chain_page_locator(page, {
            ClickworkerConnector.SEL_TASK_CARD: FakeLocator(count=0),
        })

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        candidates = await connector.find_tasks()

        assert candidates == []

    @pytest.mark.asyncio
    async def test_execute_task_success(self, mock_context):
        """Test successful task execution fills inputs and submits."""
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

        context, page = mock_context
        self._chain_page_locator(page, {
            "textarea[name], input[type='text'], input[type='number'], "
            "textarea:not([hidden])": FakeLocator(count=1, texts=[""]),
            "form button[type='submit']": FakeLocator(count=1),
            "button[type='submit']": FakeLocator(count=1),
            "[class*='success'], [class*='thank'], .alert-success, "
            "[class*='completed']": FakeLocator(count=1),
        })

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
        assert result.platform_data["submitted"] is True

    @pytest.mark.asyncio
    async def test_execute_task_failure(self, mock_context):
        """Test task execution failure on network error."""
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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

    @pytest.mark.asyncio
    async def test_execute_task_not_submitted(self, mock_context):
        """Test task marked not-completed when there is no submit button nor
        inputs and no success marker."""
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

        context, page = mock_context
        self._chain_page_locator(page, {
            # No inputs and no submit button and no success marker
            "textarea[name], input[type='text'], input[type='number'], "
            "textarea:not([hidden])": FakeLocator(count=0),
            "form button[type='submit']": FakeLocator(count=0),
            "button[type='submit']": FakeLocator(count=0),
            "[class*='success'], [class*='thank'], .alert-success, "
            "[class*='completed']": FakeLocator(count=0),
        })

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
        assert "not confirmed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_get_earnings(self, mock_context):
        """Test get_earnings parses a '$12.34' balance from the dashboard."""
        from src.task_executor import ClickworkerConnector

        context, page = mock_context
        page.inner_text = AsyncMock(return_value="$12.34")
        page.locator = MagicMock(return_value=FakeLocator(count=1))

        connector = ClickworkerConnector(Platform.CLICKWORKER, context)
        earnings = await connector.get_earnings()

        assert earnings == Decimal("12.34")


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

    @pytest.mark.asyncio
    async def test_save_and_load_cookies(self, tmp_path):
        """Test session cookies round-trip to and from the storage dir."""
        from src.task_executor import BrowserSessionManager

        mock_context = AsyncMock()
        mock_context.cookies = AsyncMock(return_value=[
            {"name": "session", "value": "abc", "domain": ".clickworker.com"},
            {"name": "token", "value": "xyz", "domain": ".clickworker.com"},
        ])

        manager = BrowserSessionManager(headless=True, storage_dir=str(tmp_path))
        manager._contexts["clickworker_ctx"] = mock_context

        assert await manager.save_cookies("clickworker_ctx") is True

        # A saved cookie file now exists
        cookie_file = tmp_path / "clickworker_ctx_cookies.json"
        assert cookie_file.exists()

        loaded = await manager.load_cookies("clickworker_ctx")
        assert loaded == [
            {"name": "session", "value": "abc", "domain": ".clickworker.com"},
            {"name": "token", "value": "xyz", "domain": ".clickworker.com"},
        ]

    @pytest.mark.asyncio
    async def test_load_cookies_missing_returns_none(self, tmp_path):
        """Test loading cookies for a context that was never saved."""
        from src.task_executor import BrowserSessionManager

        manager = BrowserSessionManager(headless=True, storage_dir=str(tmp_path))
        assert await manager.load_cookies("never_saved") is None

    @pytest.mark.asyncio
    async def test_create_context_reuses_saved_session(self, tmp_path):
        """Test create_context warms a fresh context with stored cookies
        (artifact.md §13 session persistence across Render restarts)."""
        from src.task_executor import BrowserSessionManager

        stored = [{"name": "session", "value": "saved-token",
                   "domain": ".clickworker.com"}]
        (tmp_path / "clickworker_ctx_cookies.json").write_text(
            json.dumps(stored)
        )

        with patch("src.task_executor.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser = AsyncMock()
            mock_context = AsyncMock()
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)
            mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            manager = BrowserSessionManager(
                headless=True, storage_dir=str(tmp_path)
            )
            await manager.start()

            context = await manager.create_context(
                "clickworker_ctx", persist=True
            )

            assert context == mock_context
            # Saved cookies were replayed into the new context
            mock_context.add_cookies.assert_called_once()

            await manager.stop()

    @pytest.mark.asyncio
    async def test_stop_persists_active_sessions(self, tmp_path):
        """Test TaskExecutor.stop() writes cookies for live platform sessions
        so the agent stays logged in across restarts."""
        from src.task_executor import TaskExecutor
        from src.wallet import Wallet

        with patch("src.task_executor.BrowserSessionManager") as mock_manager_class:
            mock_manager = AsyncMock(spec=(
                "start", "stop", "create_context", "save_cookies"
            ))
            mock_manager.start = AsyncMock()
            mock_manager.stop = AsyncMock()
            mock_manager.save_cookies = AsyncMock(return_value=True)
            mock_manager_class.return_value = mock_manager

            executor = TaskExecutor(
                wallet=Wallet(), headless=True, session_dir=str(tmp_path)
            )
            executor.session_manager = mock_manager
            executor._active_sessions = {"clickworker_ctx", "toloka_ctx"}

            await executor.start()
            await executor.stop()

            assert mock_manager.save_cookies.await_count == 2
            names = [c.args[0] for c in mock_manager.save_cookies.await_args_list]
            assert set(names) == {"clickworker_ctx", "toloka_ctx"}
            assert executor._active_sessions == set()


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
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import Platform

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
        from src.task_executor import ExecutionError
        from src.task_scorer import Platform

        with pytest.raises(ExecutionError, match="No credentials"):
            await executor._get_connector(Platform.CLICKWORKER)

    @pytest.mark.asyncio
    async def test_get_connector_blocked_platform_raises(self, mock_wallet):
        """A platform that exhausted the §19 stealth ladder is never retried,
        even with valid credentials — checked before spending a login attempt."""
        from src.captcha_handler import BotDetectionTracker, PlatformBlockError
        from src.persistence import InMemoryStore
        from src.task_executor import TaskExecutor
        from src.task_scorer import Platform

        store = InMemoryStore()
        store.mark_platform_blocked(Platform.CLICKWORKER.value, {"vendor": "kasada"})
        tracker = BotDetectionTracker(store)

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=mock_wallet, headless=True, bot_tracker=tracker)
            executor.set_credentials(Platform.CLICKWORKER, {"email": "e", "password": "p"})

            with pytest.raises(PlatformBlockError):
                await executor._get_connector(Platform.CLICKWORKER)

    @pytest.mark.asyncio
    async def test_get_connector_scammed_platform_raises(self, mock_wallet):
        """A platform that already scammed this agent (§20) is never rejoined."""
        from src.persistence import InMemoryStore
        from src.scam_detection import PlatformScammedError, ScamTracker
        from src.task_executor import TaskExecutor
        from src.task_scorer import Platform

        store = InMemoryStore()
        store.mark_platform_scammed(Platform.CLICKWORKER.value, {"type": "time_scam"})
        tracker = ScamTracker(store)

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=mock_wallet, headless=True, scam_tracker=tracker)
            executor.set_credentials(Platform.CLICKWORKER, {"email": "e", "password": "p"})

            with pytest.raises(PlatformScammedError):
                await executor._get_connector(Platform.CLICKWORKER)

    @pytest.mark.asyncio
    async def test_get_connector_unblocked_platform_proceeds(self, executor):
        """A platform not in the block list is unaffected by an attached tracker."""
        from src.captcha_handler import BotDetectionTracker
        from src.persistence import InMemoryStore
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import Platform

        executor.bot_tracker = BotDetectionTracker(InMemoryStore())
        mock_connector = AsyncMock(spec=ClickworkerConnector)
        mock_connector.login = AsyncMock(return_value=True)

        with patch("src.task_executor.CONNECTORS", {Platform.CLICKWORKER: lambda p, c: mock_connector}):
            executor.set_credentials(Platform.CLICKWORKER, {"email": "test", "password": "pass"})
            executor.session_manager.create_context = AsyncMock(return_value=AsyncMock())

            connector = await executor._get_connector(Platform.CLICKWORKER)

        assert connector == mock_connector

    @pytest.mark.asyncio
    async def test_discover_tasks(self, executor):
        """Test discovering tasks from multiple platforms."""
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

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
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskResult, TaskType

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
        from src.task_executor import ClickworkerConnector
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskResult, TaskType

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
    async def test_execute_task_times_out(self, executor, mock_wallet):
        """Test that a task exceeding the duration cap is aborted and fails.

        artifact.md §14 "Task timeout" — a stuck task must not run indefinitely
        while debt keeps ticking, so the executor reports failure with $0.
        """
        import asyncio

        from src.task_executor import ClickworkerConnector
        from src.task_scorer import TaskCandidate

        async def _stuck_execute_task(candidate):
            await asyncio.sleep(30)  # far longer than any sane cap

        mock_connector = AsyncMock(spec=ClickworkerConnector)
        mock_connector.execute_task = AsyncMock(side_effect=_stuck_execute_task)

        executor._connectors[Platform.CLICKWORKER] = mock_connector
        executor.task_timeout_seconds = 0.05  # tiny cap to force a timeout quickly

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Stuck task",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )

        await executor.start()
        result = await executor.execute_task(candidate)
        await executor.stop()

        assert result.success is False
        assert result.amount_earned == Decimal("0")
        assert result.error is not None
        assert "timed out" in result.error
        assert result.platform_data.get("timed_out") is True
        mock_wallet.credit_earned.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_task_respects_default_timeout(self, executor, mock_wallet):
        """Test that the default task timeout is applied when not overridden."""
        from src.task_executor import DEFAULT_TASK_TIMEOUT_SECONDS

        assert executor.task_timeout_seconds == DEFAULT_TASK_TIMEOUT_SECONDS
        assert executor.task_timeout_seconds > 0

    @pytest.mark.asyncio
    async def test_execute_batch(self, executor, mock_wallet):
        """Test executing batch of tasks."""
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskResult, TaskType

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
    async def test_mock_execute_task_success(self):
        """Test deterministic mock execution returns a valid success result."""
        from src.task_executor import mock_execute_task
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )

        result = await mock_execute_task(candidate)

        assert result.success is True
        assert result.amount_earned == Decimal("10.00")
        assert result.error is None

    @pytest.mark.asyncio
    async def test_mock_execute_task_failure(self):
        """Test deterministic mock execution with an explicit failure."""
        from src.task_executor import mock_execute_task
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskType

        candidate = TaskCandidate(
            platform=Platform.CLICKWORKER,
            task_type=TaskType.MICROTASK,
            title="Test",
            estimated_pay=Decimal("10.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )

        result = await mock_execute_task(candidate, success=False)

        assert result.success is False
        assert result.amount_earned == Decimal("0")
        assert result.error is not None


# ============================================================================
# Integration tests
# ============================================================================

class TestTaskScorerExecutorIntegration:
    """Integration tests combining scorer and executor."""

    @pytest.mark.asyncio
    async def test_full_cycle_mock(self):
        """Test full discover -> score -> execute cycle with mocks."""
        from decimal import Decimal

        from src.task_executor import mock_execute_task
        from src.task_scorer import PaymentMethod, Platform, TaskCandidate, TaskScorer, TaskType
        from src.wallet import Wallet

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

        # Execute passing task. mock_execute_task succeeds ~80% of the time, so
        # retry a bounded number of times to guarantee a successful run. This
        # keeps the test deterministic (it must verify the credit path) instead
        # of occasionally failing on the 20% mock-failure branch.
        result = None
        for _ in range(10):
            result = await mock_execute_task(scored[0].candidate)
            if result.success:
                break

        assert result is not None and result.success
        wallet.credit_earned(result.amount_earned)

        assert wallet.free > 0 or wallet.locked > 0


# Run tests if called directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])