"""Unit tests for the ethical guardrail hard blacklist (artifact.md §6/§14)."""

import pytest
from decimal import Decimal

from src.guardrails import (
    EthicalGuardrail,
    GuardrailCategory,
    GuardrailVerdict,
    BLACKLIST_RULES,
    get_guardrail,
)
from src.task_scorer import (
    TaskCandidate,
    TaskScorer,
    Platform,
    TaskType,
    PaymentMethod,
)


def _candidate(
    title="Categorize product images",
    description="Tag 100 images into predefined categories",
    platform=Platform.CLICKWORKER,
    **kwargs,
):
    defaults = dict(
        task_type=TaskType.MICROTASK,
        estimated_pay=Decimal("10.00"),
        estimated_hours=Decimal("1.0"),
        payment_method=PaymentMethod.PAYONEER,
        platform_certainty=Decimal("0.80"),
    )
    defaults.update(kwargs)
    return TaskCandidate(
        platform=platform,
        title=title,
        description=description,
        **defaults,
    )


class TestEthicalGuardrail:
    """Test the guardrail's rule engine in isolation."""

    @pytest.fixture
    def guardrail(self):
        return EthicalGuardrail()

    def test_allows_clean_task(self, guardrail):
        verdict = guardrail.evaluate(_candidate())
        assert verdict.allowed is True
        assert verdict.category is None
        assert verdict.matched_rules == []

    def test_rejects_spam(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="Post spam comments on blogs for cash"))
        assert verdict.allowed is False
        assert verdict.category == GuardrailCategory.SPAM
        assert verdict.reason

    def test_rejects_bulk_email(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="Send bulk email campaigns"))
        assert verdict.allowed is False
        assert verdict.category == GuardrailCategory.SPAM

    def test_rejects_fake_review(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="Write fake reviews for Amazon products"))
        assert verdict.allowed is False
        assert verdict.category == GuardrailCategory.FAKE_REVIEW

    def test_rejects_plagiarism(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="Rewrite articles by scraping other sites"))
        assert verdict.allowed is False
        assert verdict.category == GuardrailCategory.PLAGIARISM

    def test_rejects_tos_violation(self, guardrail):
        verdict = guardrail.evaluate(_candidate(description="Bypass bot detection to automate registrations"))
        assert verdict.allowed is False
        assert verdict.category == GuardrailCategory.TOS_VIOLATION

    def test_rejects_illegal(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="Hack into accounts and leak data"))
        assert verdict.allowed is False
        assert verdict.category == GuardrailCategory.ILLEGAL

    def test_rejects_fraud(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="Money laundering scheme participation"))
        assert verdict.allowed is False
        assert verdict.category == GuardrailCategory.ILLEGAL

    def test_rejects_when_in_metadata(self, guardrail):
        verdict = guardrail.evaluate(
            _candidate(title="Clean review task", metadata={"instructions": "post fake reviews"})
        )
        assert verdict.allowed is False
        assert verdict.category == GuardrailCategory.FAKE_REVIEW

    def test_rejects_case_insensitively(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="SPAM THE FORUMS"))
        assert verdict.allowed is False

    def test_monster_tracks_multiple_rules(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="spam and fake reviews and plagiarize content"))
        assert verdict.allowed is False
        assert len(verdict.matched_rules) >= 3

    def test_is_allowed_convenience(self, guardrail):
        assert guardrail.is_allowed(_candidate()) is True
        assert guardrail.is_allowed(_candidate(title="spam")) is False

    def test_empty_text_allowed(self, guardrail):
        verdict = guardrail.evaluate(_candidate(title="", description=""))
        assert verdict.allowed is True

    def test_disabled_guardrail_allows(self):
        g = EthicalGuardrail()
        g.reject_all()
        verdict = g.evaluate(_candidate(title="spam"))
        assert verdict.allowed is True

    def test_all_categories_represented_in_rules(self):
        categories = {r.category for r in BLACKLIST_RULES}
        assert categories == {
            GuardrailCategory.SPAM,
            GuardrailCategory.FAKE_REVIEW,
            GuardrailCategory.PLAGIARISM,
            GuardrailCategory.TOS_VIOLATION,
            GuardrailCategory.ILLEGAL,
        }


class TestGuardrailIntegrationScorer:
    """Guardrail enforced inside TaskScorer."""

    def test_blacklisted_never_passes_even_in_thriving(self):
        scorer = TaskScorer()
        # Even with high pay and high certainty, a blacklisted task is rejected.
        candidate = _candidate(
            title="Write fake reviews, good pay",
            estimated_pay=Decimal("100.00"),
            platform_certainty=Decimal("1.0"),
        )
        score = scorer.score(candidate, Decimal("1.00"))
        assert score.passes_threshold is False
        assert score.guardrail.allowed is False
        assert any("BLOCKED" in r or "REJECT" in r for r in score.reasoning)

    def test_blacklisted_never_passes_in_terminal(self):
        """The critical guarantee: even in Terminal state (threshold 0.50),
        a blacklisted task is still rejected."""
        scorer = TaskScorer()
        candidate = _candidate(
            title="Build a phishing page",
            estimated_pay=Decimal("500.00"),
            platform_certainty=Decimal("1.0"),
        )
        score = scorer.score(candidate, Decimal("9.75"))  # Terminal
        assert score.survival_state.value == "terminal"
        assert score.threshold_used == Decimal("0.50")
        assert score.final_score >= Decimal("0.90")  # would otherwise pass
        assert score.passes_threshold is False
        assert score.guardrail.allowed is False

    def test_clean_task_still_passes_in_terminal(self):
        scorer = TaskScorer()
        candidate = _candidate()  # clean, high certainty
        score = scorer.score(candidate, Decimal("9.75"))
        assert score.guardrail.allowed is True
        assert score.passes_threshold is True

    def test_filter_executable_drops_blacklisted(self):
        scorer = TaskScorer()
        good = _candidate(title="Tag product images")
        bad = _candidate(title="Post spam to forums")
        executable = scorer.filter_executable([good, bad], Decimal("1.00"))
        assert [s.candidate.title for s in executable] == ["Tag product images"]

    def test_greylist_crypto_platform_not_blacklisted(self):
        """Crypto earnings/affiliate are greylist (low priority), NOT blacklist."""
        scorer = TaskScorer()
        candidate = TaskCandidate(
            platform=Platform.DATA_ANNOTATION,
            task_type=TaskType.DATA_ANNOTATION,
            title="Label training data",
            description="Complete RLHF annotation tasks",
            estimated_pay=Decimal("30.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.CRYPTO,
            platform_certainty=Decimal("0.65"),
            metadata={"crypto": True, "affiliate": False},
        )
        score = scorer.score(candidate, Decimal("1.00"))
        assert score.guardrail.allowed is True


class TestGuardrailIntegrationExecutor:
    """Guardrail double-check inside TaskExecutor.execute_task."""

    @pytest.mark.asyncio
    async def test_executor_blocks_blacklisted_before_connector(self):
        from unittest.mock import MagicMock, AsyncMock, patch
        from src.task_executor import TaskExecutor
        from src.task_scorer import TaskResult

        # A blacklisted candidate that somehow reached execution.
        candidate = _candidate(title="Post spam to forums")

        wallet = MagicMock()
        wallet.credit_earned = MagicMock(return_value={
            "debt_repaid": Decimal("0"), "to_free": Decimal("0"), "to_locked": Decimal("0"),
        })

        executor = TaskExecutor(wallet=wallet, headless=True)
        executor._running = True  # skip start()

        # Guardrail should block before connector is even touched.
        result = await executor.execute_task(candidate)

        assert result.success is False
        assert result.amount_earned == Decimal("0")
        assert "guardrail" in result.error.lower()
        assert "guardrail" in result.platform_data

    @pytest.mark.asyncio
    async def test_executor_allows_clean_task_past_guardrail(self):
        from unittest.mock import MagicMock, AsyncMock, patch
        from src.task_executor import TaskExecutor, ClickworkerConnector
        from src.task_scorer import TaskResult

        candidate = _candidate()

        wallet = MagicMock()
        wallet.credit_earned = MagicMock(return_value={
            "debt_repaid": Decimal("0"), "to_free": Decimal("10.00"), "to_locked": Decimal("0"),
        })

        mock_connector = AsyncMock(spec=ClickworkerConnector)
        mock_connector.execute_task = AsyncMock(return_value=TaskResult(
            task_id="t1", candidate=candidate, success=True,
            amount_earned=Decimal("10.00"), time_spent_hours=Decimal("1.0"),
        ))

        executor = TaskExecutor(wallet=wallet, headless=True)
        executor._running = True
        executor._connectors[Platform.CLICKWORKER] = mock_connector
        executor.set_credentials(Platform.CLICKWORKER, {"email": "e", "password": "p"})

        result = await executor.execute_task(candidate)
        assert result.success is True
        mock_connector.execute_task.assert_awaited_once()

    def test_guardrail_singleton(self):
        assert get_guardrail() is get_guardrail()
