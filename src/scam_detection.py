"""Scam handling system (artifact.md §20).

Deterministic, framework-free — mirrors the design of ``captcha_handler.py``
and ``guardrails.py``. Research (DDG search, legitimacy judgment from review
text) is the caller's job; this module only owns the parts that must be
correct and testable without a network call:

- ``score_legitimacy`` / ``legitimacy_gate``: turn structured pre-join
  signals into a 0.0-1.0 score and a join/cap/blacklist decision.
- ``PaymentWindow`` / ``ScamTracker``: track per-task payment deadlines and
  flag overdue tasks for the (external) scam-vs-delay research step.
- ``ScamTracker.record_scam`` / ``resolve_chargeback``: the deterministic
  consequences of a *confirmed* scam — permanent platform blacklist, wallet
  reversal on chargeback.
- ``enforce_no_upfront_payment``: the absolute rule from §20 — there is no
  parameter that makes this NOT raise. Any code path that reaches it means
  an upfront payment was about to be attempted, and it must be refused.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ScamPreventionError(Exception):
    """Raised when a hardcoded scam-prevention rule (§20) would be violated."""


class PlatformScammedError(Exception):
    """Raised when attempting to work a platform that already scammed this agent."""


# ---------------------------------------------------------------------------
# Pre-join legitimacy scoring
# ---------------------------------------------------------------------------

class LegitimacyGate(str, Enum):
    JOIN = "join"
    JOIN_CAPPED = "join_capped"
    BLACKLIST = "blacklist"


@dataclass
class PlatformSignals:
    """Structured pre-join signals gathered by the (external) research step."""

    domain_age_days: int
    has_https: bool
    upfront_payment_required: bool
    has_verifiable_payment_proof: bool
    has_reddit_or_review_presence: bool
    rate_multiple_of_market: Decimal = Decimal("1")
    anonymous_ownership: bool = False


def score_legitimacy(signals: PlatformSignals) -> Decimal:
    """Score a platform 0.0 (scam-like) to 1.0 (legitimate).

    Upfront payment is an automatic hard fail regardless of every other
    signal — §20 rule 4 ("never ignore a legitimacy score < 0.60").
    """
    if signals.upfront_payment_required:
        return Decimal("0.0")

    score = Decimal("1.0")
    if not signals.has_https:
        score -= Decimal("0.30")
    if signals.domain_age_days < 180:
        score -= Decimal("0.25")
    if not signals.has_verifiable_payment_proof:
        score -= Decimal("0.25")
    if not signals.has_reddit_or_review_presence:
        score -= Decimal("0.15")
    if signals.anonymous_ownership:
        score -= Decimal("0.20")
    if signals.rate_multiple_of_market > 5:
        score -= Decimal("0.30")

    return max(Decimal("0.0"), min(Decimal("1.0"), score))


def legitimacy_gate(score: Decimal) -> LegitimacyGate:
    if score < Decimal("0.60"):
        return LegitimacyGate.BLACKLIST
    if score < Decimal("0.80"):
        return LegitimacyGate.JOIN_CAPPED
    return LegitimacyGate.JOIN


# ---------------------------------------------------------------------------
# Payment window monitoring
# ---------------------------------------------------------------------------

class PlatformType(str, Enum):
    MICRO_TASK = "micro_task"
    FREELANCE = "freelance"
    PASSIVE_SALES = "passive_sales"


# (expected window, grace period) per §20 table
_WINDOWS: dict[PlatformType, tuple[timedelta, timedelta]] = {
    PlatformType.MICRO_TASK: (timedelta(hours=72), timedelta(hours=24)),
    PlatformType.FREELANCE: (timedelta(days=14), timedelta(hours=48)),
    PlatformType.PASSIVE_SALES: (timedelta(days=30), timedelta(days=7)),
}


@dataclass
class PaymentWindow:
    task_id: str
    platform: str
    platform_type: PlatformType
    started_at: datetime
    paid: bool = False

    def deadline(self) -> datetime:
        window, _grace = _WINDOWS[self.platform_type]
        return self.started_at + window

    def grace_deadline(self) -> datetime:
        window, grace = _WINDOWS[self.platform_type]
        return self.started_at + window + grace

    def is_overdue(self, now: datetime) -> bool:
        """Past the expected window but still inside grace — suspected, not confirmed."""
        return not self.paid and now > self.deadline()

    def is_grace_exceeded(self, now: datetime) -> bool:
        """Past window + grace — treat as confirmed scam per the §20 protocol."""
        return not self.paid and now > self.grace_deadline()


# ---------------------------------------------------------------------------
# Scam events & response
# ---------------------------------------------------------------------------

class ScamType(str, Enum):
    TIME_SCAM = "time_scam"
    MONEY_SCAM = "money_scam"
    BAIT_AND_SWITCH = "bait_and_switch"
    FAKE_REJECTION = "fake_rejection"
    CREDENTIAL_THEFT = "credential_theft"
    CHARGEBACK = "chargeback"


@dataclass
class ScamEvent:
    platform: str
    scam_type: ScamType
    life: int
    hours_wasted: Decimal = Decimal("0")
    debt_accumulated_during: Decimal = Decimal("0")
    amount_lost: Decimal = Decimal("0")
    red_flags_missed: list[str] = field(default_factory=list)
    lesson: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "type": self.scam_type.value,
            "life": self.life,
            "hours_wasted": str(self.hours_wasted),
            "debt_accumulated_during": str(self.debt_accumulated_during),
            "amount_lost": str(self.amount_lost),
            "red_flags_missed": list(self.red_flags_missed),
            "lesson": self.lesson,
        }


class ScamTracker:
    """Tracks payment windows and confirmed scams; drives the §20 consequences."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._windows: dict[str, PaymentWindow] = {}

    # -- payment windows ------------------------------------------------

    def register_task(self, window: PaymentWindow) -> None:
        self._windows[window.task_id] = window

    def mark_paid(self, task_id: str) -> None:
        if task_id in self._windows:
            self._windows[task_id].paid = True

    def unpaid_platforms(self) -> set[str]:
        """Distinct platforms with at least one outstanding payment window."""
        return {w.platform for w in self._windows.values() if not w.paid}

    def mark_platform_paid(self, platform: str) -> int:
        """Mark every outstanding window for ``platform`` as paid.

        Used when a payment-alert email confirms a payout arrived but the
        email itself carries no task_id — the platform-level signal is all
        we have, so every window still open for it is resolved at once.
        Returns the number of windows resolved.
        """
        resolved = 0
        for window in self._windows.values():
            if window.platform == platform and not window.paid:
                window.paid = True
                resolved += 1
        return resolved

    def overdue_tasks(self, now: Optional[datetime] = None) -> list[PaymentWindow]:
        """Tasks past their expected window (suspected, not yet confirmed)."""
        now = now or datetime.utcnow()
        return [w for w in self._windows.values() if w.is_overdue(now)]

    def grace_exceeded_tasks(self, now: Optional[datetime] = None) -> list[PaymentWindow]:
        """Tasks past window + grace — treat as confirmed scams per §20."""
        now = now or datetime.utcnow()
        return [w for w in self._windows.values() if w.is_grace_exceeded(now)]

    # -- blacklist --------------------------------------------------------

    def is_platform_scammed(self, platform: str) -> bool:
        return bool(self._store.is_platform_scammed(platform))

    def record_scam(self, event: ScamEvent) -> None:
        self._store.mark_platform_scammed(event.platform, event.to_dict())
        logger.warning(f"Confirmed scam on {event.platform}: {event.scam_type.value}")

    # -- wallet consequence -------------------------------------------------

    def resolve_chargeback(self, wallet: Any, amount: Decimal) -> dict[str, Decimal]:
        """A previously-credited payment was clawed back — reverse the credit."""
        return wallet.reverse_credit(amount)


# ---------------------------------------------------------------------------
# Hardcoded rules (never overridden, even in Terminal survival state)
# ---------------------------------------------------------------------------

def enforce_no_upfront_payment(context: str = "") -> None:
    """§20 rule 1: NEVER pay upfront for anything, ever.

    There is no threshold, survival state, or certainty score that permits
    this — call it on every code path that would spend money to "unlock"
    access to a platform or task, and let it refuse unconditionally.
    """
    suffix = f" — {context}" if context else ""
    raise ScamPreventionError(f"Refused: upfront payment is never permitted (§20 rule 1){suffix}")
