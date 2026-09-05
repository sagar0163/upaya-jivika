"""Tests for src/scam_detection.py — scam handling system (artifact.md §20)."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.persistence import InMemoryStore
from src.scam_detection import (
    LegitimacyGate,
    PaymentWindow,
    PlatformSignals,
    PlatformType,
    ScamEvent,
    ScamPreventionError,
    ScamTracker,
    ScamType,
    enforce_no_upfront_payment,
    legitimacy_gate,
    score_legitimacy,
)
from src.wallet import Wallet


def _clean_signals(**overrides: object) -> PlatformSignals:
    base = dict(
        domain_age_days=400,
        has_https=True,
        upfront_payment_required=False,
        has_verifiable_payment_proof=True,
        has_reddit_or_review_presence=True,
    )
    base.update(overrides)
    return PlatformSignals(**base)  # type: ignore[arg-type]


class TestScoreLegitimacy:
    def test_clean_platform_scores_perfect(self):
        assert score_legitimacy(_clean_signals()) == Decimal("1.0")

    def test_upfront_payment_forces_zero_regardless_of_other_signals(self):
        signals = _clean_signals(upfront_payment_required=True)
        assert score_legitimacy(signals) == Decimal("0.0")

    def test_new_domain_penalized(self):
        signals = _clean_signals(domain_age_days=30)
        assert score_legitimacy(signals) == Decimal("0.75")

    def test_no_https_penalized(self):
        signals = _clean_signals(has_https=False)
        assert score_legitimacy(signals) == Decimal("0.70")

    def test_no_payment_proof_penalized(self):
        signals = _clean_signals(has_verifiable_payment_proof=False)
        assert score_legitimacy(signals) == Decimal("0.75")

    def test_no_review_presence_penalized(self):
        signals = _clean_signals(has_reddit_or_review_presence=False)
        assert score_legitimacy(signals) == Decimal("0.85")

    def test_anonymous_ownership_penalized(self):
        signals = _clean_signals(anonymous_ownership=True)
        assert score_legitimacy(signals) == Decimal("0.80")

    def test_too_good_to_be_true_rate_penalized(self):
        signals = _clean_signals(rate_multiple_of_market=Decimal("6"))
        assert score_legitimacy(signals) == Decimal("0.70")

    def test_score_floors_at_zero(self):
        signals = _clean_signals(
            domain_age_days=10,
            has_https=False,
            has_verifiable_payment_proof=False,
            has_reddit_or_review_presence=False,
            anonymous_ownership=True,
            rate_multiple_of_market=Decimal("10"),
        )
        assert score_legitimacy(signals) == Decimal("0.0")


class TestLegitimacyGate:
    def test_high_score_joins(self):
        assert legitimacy_gate(Decimal("0.85")) is LegitimacyGate.JOIN

    def test_boundary_080_joins(self):
        assert legitimacy_gate(Decimal("0.80")) is LegitimacyGate.JOIN

    def test_mid_score_joins_capped(self):
        assert legitimacy_gate(Decimal("0.70")) is LegitimacyGate.JOIN_CAPPED

    def test_boundary_060_joins_capped(self):
        assert legitimacy_gate(Decimal("0.60")) is LegitimacyGate.JOIN_CAPPED

    def test_low_score_blacklisted(self):
        assert legitimacy_gate(Decimal("0.59")) is LegitimacyGate.BLACKLIST

    def test_zero_score_blacklisted(self):
        assert legitimacy_gate(Decimal("0.0")) is LegitimacyGate.BLACKLIST


class TestPaymentWindow:
    def test_not_overdue_before_deadline(self):
        window = PaymentWindow(
            task_id="t1",
            platform="clickworker",
            platform_type=PlatformType.MICRO_TASK,
            started_at=datetime(2026, 1, 1),
        )
        now = datetime(2026, 1, 1) + timedelta(hours=71)
        assert window.is_overdue(now) is False

    def test_overdue_after_window_but_within_grace(self):
        window = PaymentWindow(
            task_id="t1",
            platform="clickworker",
            platform_type=PlatformType.MICRO_TASK,
            started_at=datetime(2026, 1, 1),
        )
        now = datetime(2026, 1, 1) + timedelta(hours=80)
        assert window.is_overdue(now) is True
        assert window.is_grace_exceeded(now) is False

    def test_grace_exceeded_after_window_plus_grace(self):
        window = PaymentWindow(
            task_id="t1",
            platform="clickworker",
            platform_type=PlatformType.MICRO_TASK,
            started_at=datetime(2026, 1, 1),
        )
        now = datetime(2026, 1, 1) + timedelta(hours=97)
        assert window.is_grace_exceeded(now) is True

    def test_paid_task_never_overdue(self):
        window = PaymentWindow(
            task_id="t1",
            platform="clickworker",
            platform_type=PlatformType.MICRO_TASK,
            started_at=datetime(2020, 1, 1),
            paid=True,
        )
        assert window.is_overdue(datetime(2030, 1, 1)) is False
        assert window.is_grace_exceeded(datetime(2030, 1, 1)) is False

    def test_freelance_window_is_longer(self):
        window = PaymentWindow(
            task_id="t2",
            platform="upwork",
            platform_type=PlatformType.FREELANCE,
            started_at=datetime(2026, 1, 1),
        )
        now = datetime(2026, 1, 1) + timedelta(hours=80)
        assert window.is_overdue(now) is False


class TestScamTracker:
    def test_overdue_tasks_lists_unpaid_past_deadline(self):
        tracker = ScamTracker(InMemoryStore())
        window = PaymentWindow(
            task_id="t1",
            platform="clickworker",
            platform_type=PlatformType.MICRO_TASK,
            started_at=datetime.utcnow() - timedelta(hours=80),
        )
        tracker.register_task(window)
        assert tracker.overdue_tasks() == [window]

    def test_mark_paid_removes_from_overdue(self):
        tracker = ScamTracker(InMemoryStore())
        window = PaymentWindow(
            task_id="t1",
            platform="clickworker",
            platform_type=PlatformType.MICRO_TASK,
            started_at=datetime.utcnow() - timedelta(hours=80),
        )
        tracker.register_task(window)
        tracker.mark_paid("t1")
        assert tracker.overdue_tasks() == []

    def test_grace_exceeded_tasks(self):
        tracker = ScamTracker(InMemoryStore())
        window = PaymentWindow(
            task_id="t1",
            platform="clickworker",
            platform_type=PlatformType.MICRO_TASK,
            started_at=datetime.utcnow() - timedelta(hours=97),
        )
        tracker.register_task(window)
        assert tracker.grace_exceeded_tasks() == [window]

    def test_record_scam_blacklists_platform_permanently(self):
        store = InMemoryStore()
        tracker = ScamTracker(store)
        event = ScamEvent(
            platform="scammy.io",
            scam_type=ScamType.TIME_SCAM,
            life=1,
            hours_wasted=Decimal("8"),
            lesson="No reddit presence = no trust.",
        )
        tracker.record_scam(event)
        assert tracker.is_platform_scammed("scammy.io") is True
        assert store.is_platform_scammed("scammy.io") is True

    def test_scammed_platform_survives_clear(self):
        store = InMemoryStore()
        tracker = ScamTracker(store)
        tracker.record_scam(ScamEvent(platform="scammy.io", scam_type=ScamType.TIME_SCAM, life=1))
        store.clear()
        assert store.is_platform_scammed("scammy.io") is True

    def test_resolve_chargeback_reverses_wallet_credit(self):
        tracker = ScamTracker(InMemoryStore())
        wallet = Wallet(free=Decimal("10.00"))
        result = tracker.resolve_chargeback(wallet, Decimal("4.00"))
        assert wallet.free == Decimal("6.00")
        assert result["from_free"] == Decimal("4.00")
        assert result["added_debt"] == Decimal("0")

    def test_resolve_chargeback_adds_debt_when_free_insufficient(self):
        tracker = ScamTracker(InMemoryStore())
        wallet = Wallet(free=Decimal("2.00"), debt=Decimal("0"))
        tracker.resolve_chargeback(wallet, Decimal("5.00"))
        assert wallet.free == Decimal("0.00")
        assert wallet.debt == Decimal("3.00")


class TestWalletReverseCredit:
    def test_reverse_credit_requires_positive_amount(self):
        wallet = Wallet(free=Decimal("10.00"))
        with pytest.raises(Exception):
            wallet.reverse_credit(Decimal("-1"))


class TestEnforceNoUpfrontPayment:
    def test_always_raises(self):
        with pytest.raises(ScamPreventionError):
            enforce_no_upfront_payment()

    def test_raises_with_context_in_message(self):
        with pytest.raises(ScamPreventionError, match="unlock task access"):
            enforce_no_upfront_payment("unlock task access")

    def test_raises_even_in_terminal_state_framing(self):
        # No parameter exists to bypass this — that's the point of §20 rule 1.
        with pytest.raises(ScamPreventionError):
            enforce_no_upfront_payment("Terminal state, $9.80 debt, 6 hours left")
