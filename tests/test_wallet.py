"""Unit tests for wallet.py — dual-pool wallet."""

import pytest
from decimal import Decimal

from src.wallet import Wallet, WalletError, SpendRequest


class TestCreditEarned:
    """Earnings should pay down debt first, then surplus to free pool."""

    def test_no_debt_goes_to_free(self):
        w = Wallet()
        result = w.credit_earned(10.0)
        assert w.free == Decimal("10.00")
        assert w.locked == Decimal("0.00")
        assert result["to_free"] == Decimal("10.00")

    def test_debt_repaid_first(self):
        w = Wallet(debt=Decimal("5.00"))
        result = w.credit_earned(7.0)
        assert w.debt == Decimal("0.00")
        assert w.locked == Decimal("5.00")  # debt payments → locked
        assert w.free == Decimal("2.00")
        assert result["debt_repaid"] == Decimal("5.00")

    def test_partial_debt_repayment(self):
        w = Wallet(debt=Decimal("10.00"))
        w.credit_earned(3.0)
        assert w.debt == Decimal("7.00")
        assert w.locked == Decimal("3.00")
        assert w.free == Decimal("0.00")

    def test_zero_credit_raises(self):
        w = Wallet()
        with pytest.raises(WalletError):
            w.credit_earned(0)


class TestUserWithdrawals:
    """User can withdraw from both pools."""

    def test_withdraw_free(self):
        w = Wallet(free=Decimal("10.00"))
        w.user_withdraw_free(4.0)
        assert w.free == Decimal("6.00")

    def test_withdraw_locked(self):
        w = Wallet(locked=Decimal("10.00"))
        w.user_withdraw_locked(6.0)
        assert w.locked == Decimal("4.00")

    def test_overdraw_free_raises(self):
        w = Wallet(free=Decimal("2.00"))
        with pytest.raises(WalletError):
            w.user_withdraw_free(5.0)

    def test_overdraw_locked_raises(self):
        w = Wallet(locked=Decimal("1.00"))
        with pytest.raises(WalletError):
            w.user_withdraw_locked(3.0)


class TestAISpendGate:
    """AI spend must respect debt threshold, certainty gate, and 30% cap."""

    def test_normal_spend_works(self):
        w = Wallet(free=Decimal("100.00"), debt=Decimal("1.00"))
        req = SpendRequest(amount=Decimal("5.00"), certainty=Decimal("0.96"))
        w.ai_spend(req)
        assert w.free == Decimal("95.00")

    def test_blocked_above_5_debt(self):
        w = Wallet(free=Decimal("100.00"), debt=Decimal("5.01"))
        req = SpendRequest(amount=Decimal("5.00"), certainty=Decimal("0.96"))
        with pytest.raises(WalletError, match="debt"):
            w.ai_spend(req)

    def test_blocked_at_exact_5(self):
        w = Wallet(free=Decimal("100.00"), debt=Decimal("5.00"))
        req = SpendRequest(amount=Decimal("5.00"), certainty=Decimal("0.96"))
        w.ai_spend(req)  # should work — gate is > $5, not >=

    def test_blocked_low_certainty(self):
        w = Wallet(free=Decimal("100.00"), debt=Decimal("1.00"))
        req = SpendRequest(amount=Decimal("5.00"), certainty=Decimal("0.80"))
        with pytest.raises(WalletError, match="certainty"):
            w.ai_spend(req)

    def test_blocked_exceeds_30_percent(self):
        w = Wallet(free=Decimal("100.00"), debt=Decimal("1.00"))
        req = SpendRequest(amount=Decimal("31.00"), certainty=Decimal("0.96"))
        with pytest.raises(WalletError, match="30"):
            w.ai_spend(req)

    def test_exactly_30_percent_ok(self):
        w = Wallet(free=Decimal("100.00"), debt=Decimal("1.00"))
        req = SpendRequest(amount=Decimal("30.00"), certainty=Decimal("0.96"))
        w.ai_spend(req)
        assert w.free == Decimal("70.00")


class TestLockedPoolImmutability:
    """No AI code path should touch the locked pool."""

    def test_ai_spend_does_not_reduce_locked(self):
        w = Wallet(free=Decimal("100.00"), locked=Decimal("50.00"), debt=Decimal("1.00"))
        req = SpendRequest(amount=Decimal("10.00"), certainty=Decimal("0.96"))
        w.ai_spend(req)
        assert w.locked == Decimal("50.00")

    def test_credit_to_debt_increases_locked(self):
        """Debt payments go to locked — this is the only way locked grows."""
        w = Wallet(debt=Decimal("5.00"))
        w.credit_earned(5.0)
        assert w.locked == Decimal("5.00")

    def test_locked_not_in_max_spend_calc(self):
        """30% cap is based on free pool only, not total balance."""
        w = Wallet(free=Decimal("10.00"), locked=Decimal("1000.00"), debt=Decimal("0.00"))
        req = SpendRequest(amount=Decimal("3.00"), certainty=Decimal("0.96"))
        w.ai_spend(req)
        assert w.locked == Decimal("1000.00")  # untouched


class TestProperties:
    def test_total_balance(self):
        w = Wallet(locked=Decimal("5.00"), free=Decimal("3.00"))
        assert w.total_balance == Decimal("8.00")

    def test_net_worth(self):
        w = Wallet(locked=Decimal("5.00"), free=Decimal("3.00"), debt=Decimal("2.00"))
        assert w.net_worth == Decimal("6.00")
