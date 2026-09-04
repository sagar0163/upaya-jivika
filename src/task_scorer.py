"""Task Scorer - Certainty scoring for candidate tasks from research_loop.py.

Rules from artifact.md §6-7:
- Score against platform table (§7): certainty %, pay, payment method
- Combine with survival state (§5) to produce 0.0-1.0 certainty score
- Only tasks scoring >0.85 enter execution queue (relaxed in Critical/Terminal)
- No agent frameworks — pure deterministic scoring.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional
from dataclasses import dataclass

from pydantic import BaseModel, Field

from src.guardrails import EthicalGuardrail, GuardrailVerdict, get_guardrail
from src.state_machine import State, resolve_state, min_certainty, risk_tolerance


class Platform(str, Enum):
    """Earning platforms from artifact.md §7 Active table."""
    TOLOKA = "toloka"
    CLICKWORKER = "clickworker"
    PROLIFIC = "prolific"
    APPEN = "appen"
    DATA_ANNOTATION = "dataannotation"
    UPWORK = "upwork"
    FIVERR = "fiverr"
    TEXTBROKER = "textbroker"
    SCALE_AI = "scale_ai"
    GITHUB_BOUNTIES = "github_bounties"
    GITCOIN = "gitcoin"


class TaskType(str, Enum):
    """Type of task the agent can perform."""
    MICROTASK = "microtask"           # Toloka, Clickworker
    SURVEY = "survey"                 # Prolific
    DATA_ANNOTATION = "data_annotation"  # Appen, DataAnnotation, Scale AI
    WRITING = "writing"               # Upwork, Fiverr, Textbroker
    CODING = "coding"                 # GitHub bounties, Gitcoin
    CRYPTO = "crypto"                 # Bittensor, Ocean Protocol (greylist)


class PaymentMethod(str, Enum):
    """Payment methods from artifact.md §7-8."""
    PAYONEER = "payoneer"
    PAYPAL = "paypal"
    BANK_TRANSFER = "bank_transfer"
    CRYPTO = "crypto"
    STRIPE = "stripe"
    UPI = "upi"


# Platform base data from artifact.md §7 table
# (base_certainty, typical_pay_usd, payment_method, automation_friendly)
PLATFORM_DATA: dict[Platform, tuple[Decimal, Decimal, PaymentMethod, bool]] = {
    Platform.TOLOKA: (Decimal("0.75"), Decimal("20"), PaymentMethod.PAYONEER, True),
    Platform.CLICKWORKER: (Decimal("0.70"), Decimal("30"), PaymentMethod.PAYONEER, True),
    Platform.PROLIFIC: (Decimal("0.70"), Decimal("50"), PaymentMethod.PAYPAL, True),
    Platform.APPEN: (Decimal("0.65"), Decimal("200"), PaymentMethod.PAYONEER, False),  # Project-based, less automation-friendly
    Platform.DATA_ANNOTATION: (Decimal("0.65"), Decimal("30"), PaymentMethod.CRYPTO, True),
    Platform.UPWORK: (Decimal("0.55"), Decimal("50"), PaymentMethod.PAYONEER, False),  # High competition
    Platform.FIVERR: (Decimal("0.50"), Decimal("30"), PaymentMethod.PAYONEER, False),  # 20% cut, gig-based
    Platform.TEXTBROKER: (Decimal("0.60"), Decimal("20"), PaymentMethod.BANK_TRANSFER, False),
    Platform.SCALE_AI: (Decimal("0.60"), Decimal("40"), PaymentMethod.CRYPTO, True),
    Platform.GITHUB_BOUNTIES: (Decimal("0.40"), Decimal("100"), PaymentMethod.CRYPTO, False),
    Platform.GITCOIN: (Decimal("0.40"), Decimal("100"), PaymentMethod.CRYPTO, False),
}

# Task type affinity per platform (bonus if task matches platform strength)
PLATFORM_TASK_AFFINITY: dict[Platform, list[TaskType]] = {
    Platform.TOLOKA: [TaskType.MICROTASK],
    Platform.CLICKWORKER: [TaskType.MICROTASK],
    Platform.PROLIFIC: [TaskType.SURVEY],
    Platform.APPEN: [TaskType.DATA_ANNOTATION],
    Platform.DATA_ANNOTATION: [TaskType.DATA_ANNOTATION],
    Platform.UPWORK: [TaskType.WRITING, TaskType.CODING],
    Platform.FIVERR: [TaskType.WRITING],
    Platform.TEXTBROKER: [TaskType.WRITING],
    Platform.SCALE_AI: [TaskType.DATA_ANNOTATION],
    Platform.GITHUB_BOUNTIES: [TaskType.CODING],
    Platform.GITCOIN: [TaskType.CODING],
}

# Payment method reliability for India (from artifact.md §8)
PAYMENT_RELIABILITY: dict[PaymentMethod, Decimal] = {
    PaymentMethod.PAYONEER: Decimal("1.0"),      # Native integration, no receiving fee
    PaymentMethod.PAYPAL: Decimal("0.6"),        # Restricted in India, holds common
    PaymentMethod.BANK_TRANSFER: Decimal("0.8"), # Direct but slower
    PaymentMethod.CRYPTO: Decimal("0.5"),        # 30% tax, volatility, greylist
    PaymentMethod.STRIPE: Decimal("0.7"),        # India support but newer
    PaymentMethod.UPI: Decimal("0.9"),           # Instant, but platform must support
}


class TaskCandidate(BaseModel):
    """A candidate task discovered by research_loop."""
    platform: Platform
    task_type: TaskType
    title: str
    description: str = ""
    estimated_pay: Decimal = Field(default=Decimal("0"), ge=0)
    estimated_hours: Decimal = Field(default=Decimal("1"), gt=0)
    payment_method: PaymentMethod
    platform_certainty: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)  # From research
    source_url: str = ""
    metadata: dict = Field(default_factory=dict)


class TaskScore(BaseModel):
    """Score breakdown for a task candidate."""
    candidate: TaskCandidate
    base_certainty: Decimal
    platform_bonus: Decimal
    payment_bonus: Decimal
    pay_rate_bonus: Decimal
    survival_bonus: Decimal
    final_score: Decimal
    passes_threshold: bool
    threshold_used: Decimal
    survival_state: State
    guardrail: GuardrailVerdict = GuardrailVerdict(allowed=True)
    reasoning: list[str] = Field(default_factory=list)


class TaskResult(BaseModel):
    """Result of task execution."""
    task_id: str
    candidate: TaskCandidate
    success: bool
    amount_earned: Decimal = Decimal("0")
    time_spent_hours: Decimal = Decimal("0")
    error: Optional[str] = None
    platform_data: dict = Field(default_factory=dict)


class TaskScorer:
    """Scores task candidates against platform data and survival state."""

    def __init__(
        self,
        base_threshold: Decimal = Decimal("0.85"),
        min_pay_per_hour: Decimal = Decimal("1.00"),
        guardrail: EthicalGuardrail | None = None,
    ) -> None:
        self.base_threshold = base_threshold
        self.min_pay_per_hour = min_pay_per_hour
        # Hard blacklist is absolute: never disabled by temperature/state.
        self.guardrail = guardrail or get_guardrail()

    def score(
        self,
        candidate: TaskCandidate,
        current_debt: Decimal,
    ) -> TaskScore:
        """Score a single task candidate.

        Args:
            candidate: The task candidate to score.
            current_debt: Current debt level from debt_engine.

        Returns:
            TaskScore with full breakdown.
        """
        state = resolve_state(current_debt)
        threshold = min_certainty(current_debt)
        risk = risk_tolerance(current_debt)

        reasoning = []

        # 0. Ethical guardrail — absolute hard blacklist (artifact.md §6).
        # Enforced BEFORE any scoring: a blacklisted task is rejected even in
        # Critical/Terminal states where the certainty threshold is relaxed.
        verdict = self.guardrail.evaluate(candidate)
        reasoning.append(
            f"Guardrail: {'ALLOW' if verdict.allowed else 'REJECT (' + verdict.reason + ')'}"
        )

        # 1. Base certainty from platform table
        platform_data = PLATFORM_DATA.get(candidate.platform)
        if platform_data:
            base_certainty, typical_pay, platform_payment, automation_friendly = platform_data
            reasoning.append(f"Base platform certainty: {base_certainty} ({candidate.platform.value})")
        else:
            base_certainty = Decimal("0.5")
            typical_pay = Decimal("0")
            platform_payment = candidate.payment_method
            automation_friendly = False
            reasoning.append(f"Unknown platform, using default: {base_certainty}")

        # 2. Platform-task affinity bonus
        affinity_bonus = Decimal("0")
        if candidate.task_type in PLATFORM_TASK_AFFINITY.get(candidate.platform, []):
            affinity_bonus = Decimal("0.05")
            reasoning.append(f"Task type affinity bonus: +{affinity_bonus}")

        # 3. Payment method reliability bonus
        payment_bonus = PAYMENT_RELIABILITY.get(candidate.payment_method, Decimal("0.5")) - Decimal("0.5")
        # Scale to 0-0.1 range
        payment_bonus = (payment_bonus * Decimal("0.2")).quantize(Decimal("0.01"))
        reasoning.append(f"Payment method ({candidate.payment_method.value}) bonus: +{payment_bonus}")

        # 4. Pay rate bonus (vs minimum threshold)
        pay_per_hour = candidate.estimated_pay / candidate.estimated_hours if candidate.estimated_hours > 0 else Decimal("0")
        pay_rate_bonus = Decimal("0")
        if pay_per_hour >= self.min_pay_per_hour:
            # Up to 0.1 bonus for good pay
            ratio = min(pay_per_hour / Decimal("50"), Decimal("1"))  # Cap at $50/hr
            pay_rate_bonus = (ratio * Decimal("0.1")).quantize(Decimal("0.01"))
            reasoning.append(f"Pay rate bonus: +{pay_rate_bonus} (${pay_per_hour}/hr)")
        else:
            reasoning.append(f"Low pay rate: ${pay_per_hour}/hr (min ${self.min_pay_per_hour})")

        # 5. Survival state adjustment
        # In Critical/Terminal states, we accept lower scores but don't boost — we lower threshold
        survival_bonus = Decimal("0")
        if state == State.THRIVING:
            survival_bonus = Decimal("0.05")
            reasoning.append(f"Thriving state bonus: +{survival_bonus}")
        elif state == State.SURVIVING:
            survival_bonus = Decimal("0.02")
            reasoning.append(f"Surviving state bonus: +{survival_bonus}")
        elif state == State.STRUGGLING:
            survival_bonus = Decimal("0.0")
            reasoning.append(f"Struggling state: no bonus")
        elif state in (State.CRITICAL, State.TERMINAL):
            survival_bonus = Decimal("0.0")
            reasoning.append(f"{state.value.upper()} state: threshold relaxed to {threshold}")

        # 6. Research certainty integration
        research_weight = Decimal("0.6")
        platform_weight = Decimal("0.4")

        # Combine: research certainty * weight + platform certainty * weight
        combined_certainty = (
            candidate.platform_certainty * research_weight +
            base_certainty * platform_weight
        )

        # Add bonuses
        final_score = (
            combined_certainty +
            affinity_bonus +
            payment_bonus +
            pay_rate_bonus +
            survival_bonus
        ).quantize(Decimal("0.01"))

        # Cap at 1.0
        final_score = min(final_score, Decimal("1.0"))

        # The guardrail overrides everything: blacklisted tasks never pass,
        # regardless of how high they score or how relaxed the state is.
        passes = final_score >= threshold and verdict.allowed

        if not verdict.allowed:
            reasoning.append(
                f"BLOCKED by ethical guardrail: {verdict.reason} "
                f"(final score {final_score} irrelevant)"
            )
        elif not passes:
            reasoning.append(f"FAIL: {final_score} < {threshold} (state={state.value})")
        else:
            reasoning.append(f"PASS: {final_score} >= {threshold} (state={state.value})")

        return TaskScore(
            candidate=candidate,
            base_certainty=base_certainty,
            platform_bonus=affinity_bonus,
            payment_bonus=payment_bonus,
            pay_rate_bonus=pay_rate_bonus,
            survival_bonus=survival_bonus,
            final_score=final_score,
            passes_threshold=passes,
            threshold_used=threshold,
            survival_state=state,
            guardrail=verdict,
            reasoning=reasoning,
        )

    def score_batch(
        self,
        candidates: list[TaskCandidate],
        current_debt: Decimal,
    ) -> list[TaskScore]:
        """Score multiple candidates, sorted by score descending."""
        scores = [self.score(c, current_debt) for c in candidates]
        scores.sort(key=lambda s: s.final_score, reverse=True)
        return scores

    def filter_executable(
        self,
        candidates: list[TaskCandidate],
        current_debt: Decimal,
    ) -> list[TaskScore]:
        """Return only candidates that pass the threshold."""
        return [s for s in self.score_batch(candidates, current_debt) if s.passes_threshold]


# Convenience function
async def quick_score(
    platform: Platform,
    task_type: TaskType,
    estimated_pay: Decimal,
    estimated_hours: Decimal,
    payment_method: PaymentMethod,
    current_debt: Decimal,
    platform_certainty: Decimal = Decimal("0.5"),
) -> TaskScore:
    """Quick scoring for a single task."""
    scorer = TaskScorer()
    candidate = TaskCandidate(
        platform=platform,
        task_type=task_type,
        title="Quick score task",
        estimated_pay=estimated_pay,
        estimated_hours=estimated_hours,
        payment_method=payment_method,
        platform_certainty=platform_certainty,
    )
    return scorer.score(candidate, current_debt)