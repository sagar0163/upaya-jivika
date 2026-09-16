"""Tests for src/revenue_split.py — revenue split policy + auto-payout scheduler (issue #75)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.revenue_split import (
    PayoutRecord,
    RevenueSplitEngine,
    RevenueSplitPolicy,
    SplitFractions,
    ThresholdTier,
)
from src.wallet import Wallet
from src.withdrawal import PayoutStatus, WithdrawalPool

# ---------------------------------------------------------------------------
# Policy / tier resolution
# ---------------------------------------------------------------------------

class TestRevenueSplitPolicy:
    def test_default_policy_has_all_reinvest_tier(self):
        policy = RevenueSplitPolicy()
        assert len(policy.tiers) == 1
        assert policy.tiers[0].min_balance == Decimal("0")
        assert policy.tiers[0].fractions.owner == Decimal("0")
        assert policy.tiers[0].fractions.reinvest == Decimal("1")
        assert policy.tiers[0].fractions.reserve == Decimal("0")

    def test_tiers_sorted_by_min_balance(self):
        policy = RevenueSplitPolicy(
            tiers=[
                ThresholdTier(
                    min_balance="200",
                    fractions=SplitFractions(owner="0.3", reinvest="0.5", reserve="0.2"),
                ),
                ThresholdTier(
                    min_balance="50",
                    fractions=SplitFractions(owner="0.1", reinvest="0.8", reserve="0.1"),
                ),
                ThresholdTier(
                    min_balance="0",
                    fractions=SplitFractions(owner="0", reinvest="1", reserve="0"),
                ),
            ]
        )
        balances = [t.min_balance for t in policy.tiers]
        assert balances == [Decimal("0"), Decimal("50"), Decimal("200")]

    def test_resolve_lowest_tier(self):
        policy = RevenueSplitPolicy(
            tiers=[
                ThresholdTier(
                    min_balance="0",
                    fractions=SplitFractions(owner="0", reinvest="1", reserve="0"),
                ),
                ThresholdTier(
                    min_balance="50",
                    fractions=SplitFractions(owner="0.1", reinvest="0.8", reserve="0.1"),
                ),
            ]
        )
        f = policy.resolve(Decimal("10"))
        assert f.owner == Decimal("0")
        assert f.reinvest == Decimal("1")

    def test_resolve_highest_applicable_tier(self):
        policy = RevenueSplitPolicy(
            tiers=[
                ThresholdTier(
                    min_balance="0",
                    fractions=SplitFractions(owner="0", reinvest="1", reserve="0"),
                ),
                ThresholdTier(
                    min_balance="50",
                    fractions=SplitFractions(owner="0.1", reinvest="0.8", reserve="0.1"),
                ),
                ThresholdTier(
                    min_balance="200",
                    fractions=SplitFractions(owner="0.3", reinvest="0.5", reserve="0.2"),
                ),
            ]
        )
        assert policy.resolve(Decimal("49")).owner == Decimal("0")
        assert policy.resolve(Decimal("50")).owner == Decimal("0.1")
        assert policy.resolve(Decimal("199")).owner == Decimal("0.1")
        assert policy.resolve(Decimal("200")).owner == Decimal("0.3")
        assert policy.resolve(Decimal("999")).owner == Decimal("0.3")

    def test_fraction_sum_validation(self):
        # 0.1 + 0.8 + 0.1 = 1.0 is fine
        SplitFractions(owner="0.1", reinvest="0.8", reserve="0.1")

    def test_fraction_sum_must_equal_one(self):
        with pytest.raises(Exception, match="sum to 1.0"):
            SplitFractions(owner="0.5", reinvest="0.5", reserve="0.1")


# ---------------------------------------------------------------------------
# apply_split
# ---------------------------------------------------------------------------

def _policy_with_tiers() -> RevenueSplitPolicy:
    return RevenueSplitPolicy(
        tiers=[
            ThresholdTier(
                min_balance="0",
                fractions=SplitFractions(owner="0", reinvest="1", reserve="0"),
            ),
            ThresholdTier(
                min_balance="50",
                fractions=SplitFractions(owner="0.1", reinvest="0.8", reserve="0.1"),
            ),
            ThresholdTier(
                min_balance="200",
                fractions=SplitFractions(owner="0.3", reinvest="0.5", reserve="0.2"),
            ),
        ]
    )


class TestApplySplit:
    def test_lowest_tier_all_reinvest(self):
        engine = RevenueSplitEngine(policy=_policy_with_tiers())
        wallet = Wallet()  # free 0 -> after credit free=40 (< 50 => lowest tier)
        wallet.credit_earned(Decimal("40"))
        breakdown = engine.apply_split(Decimal("40"), wallet)
        assert breakdown["owner"] == Decimal("0")
        assert breakdown["reinvest"] == Decimal("40")
        assert breakdown["reserve"] == Decimal("0")
        assert wallet.owner_owed == Decimal("0")
        assert wallet.free == Decimal("40")

    def test_mid_tier_splits(self):
        engine = RevenueSplitEngine(policy=_policy_with_tiers())
        wallet = Wallet()
        wallet.credit_earned(Decimal("100"))  # free=100, below 200 => tier 10/80/10
        breakdown = engine.apply_split(Decimal("100"), wallet)
        assert breakdown["owner"] == Decimal("10.00")
        assert breakdown["reinvest"] == Decimal("80.00")
        assert breakdown["reserve"] == Decimal("10.00")
        # free started 100, minus owner 10 minus reserve 10 => 80
        assert wallet.free == Decimal("80.00")
        assert wallet.owner_owed == Decimal("10.00")
        assert wallet.locked == Decimal("10.00")

    def test_high_tier_after_two_credits(self):
        engine = RevenueSplitEngine(policy=_policy_with_tiers())
        wallet = Wallet()
        # First payment of 250 lands in free; free=250 >= 200 => tier 30/50/20
        wallet.credit_earned(Decimal("250"))
        breakdown = engine.apply_split(Decimal("250"), wallet)
        assert breakdown["owner"] == Decimal("75.00")
        assert breakdown["reinvest"] == Decimal("125.00")
        assert breakdown["reserve"] == Decimal("50.00")
        assert wallet.free == Decimal("125.00")
        assert wallet.owner_owed == Decimal("75.00")
        assert wallet.locked == Decimal("50.00")

    def test_split_preserves_existing_balances(self):
        engine = RevenueSplitEngine(policy=_policy_with_tiers())
        wallet = Wallet(free=Decimal("100.00"), locked=Decimal("30.00"))
        wallet.credit_earned(Decimal("100"))  # free=200 -> tier 30/50/20
        breakdown = engine.apply_split(Decimal("100"), wallet)
        assert breakdown["owner"] == Decimal("30.00")
        assert breakdown["reserve"] == Decimal("20.00")
        # existing 100 free + 100 credited - 30 owner - 20 reserve = 150
        assert wallet.free == Decimal("150.00")
        assert wallet.owner_owed == Decimal("30.00")
        assert wallet.locked == Decimal("50.00")

    def test_zero_to_free_noop(self):
        engine = RevenueSplitEngine(policy=_policy_with_tiers())
        wallet = Wallet(free=Decimal("10"))
        breakdown = engine.apply_split(Decimal("0"), wallet)
        assert all(v == Decimal("0") for v in breakdown.values())


# ---------------------------------------------------------------------------
# check_auto_payout
# ---------------------------------------------------------------------------

class TestCheckAutoPayout:
    def _engine(self, enabled=True, minimum="10.00", cadence=24):
        policy = RevenueSplitPolicy(
            tiers=[
                ThresholdTier(
                    min_balance="0",
                    fractions=SplitFractions(owner="1", reinvest="0", reserve="0"),
                )
            ],
            auto_payout_enabled=enabled,
            auto_payout_minimum=minimum,
            auto_payout_cadence_hours=cadence,
        )
        return RevenueSplitEngine(policy=policy)

    def _wallet_with_owed(self, owed="50.00"):
        return Wallet(free=Decimal("0"), owner_owed=Decimal(owed))

    def test_disabled_no_payout(self):
        engine = self._engine(enabled=False)
        wallet = self._wallet_with_owed()
        result = engine.check_auto_payout(wallet, [])
        assert result is None
        assert wallet.owner_owed == Decimal("50.00")

    def test_below_minimum_no_payout(self):
        engine = self._engine(minimum="100.00")
        wallet = self._wallet_with_owed("50.00")
        result = engine.check_auto_payout(wallet, [])
        assert result is None

    def test_payout_fires_and_zeroes_owner_owed(self):
        engine = self._engine()
        wallet = self._wallet_with_owed()
        mock_client = MagicMock()
        mock_client.send_payout.return_value = (PayoutStatus.SENT, "ok")
        record = engine.check_auto_payout(wallet, [], payout_client=mock_client)
        assert record is not None
        assert record.completed is True
        assert record.amount == Decimal("50.00")
        assert wallet.owner_owed == Decimal("0.00")
        assert engine.policy.seed_capital_repaid == Decimal("50.00")

    def test_cadence_gate_blocks_immediate_second_payout(self):
        engine = self._engine()
        wallet = self._wallet_with_owed()
        mock_client = MagicMock()
        mock_client.send_payout.return_value = (PayoutStatus.SENT, "ok")
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

        record = engine.check_auto_payout(wallet, [], payout_client=mock_client, now=now)
        assert record is not None and record.completed

        # Re-accumulate and try again within the same cadence window
        wallet.owner_owed = Decimal("50.00")
        prior = [record]
        result = engine.check_auto_payout(
            wallet, prior, payout_client=mock_client,
            now=now + timedelta(hours=1),
        )
        assert result is None
        assert wallet.owner_owed == Decimal("50.00")  # untouched

    def test_cadence_elapsed_allows_payout(self):
        engine = self._engine()
        wallet = self._wallet_with_owed()
        mock_client = MagicMock()
        mock_client.send_payout.return_value = (PayoutStatus.SENT, "ok")
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        prior = PayoutRecord(
            amount=Decimal("10.00"),
            completed=True,
            initiated_at=now - timedelta(hours=25),
        )
        record = engine.check_auto_payout(
            wallet, [prior], payout_client=mock_client, now=now
        )
        assert record is not None and record.completed

    def test_failed_payout_records_error_and_keeps_owed(self):
        engine = self._engine()
        wallet = self._wallet_with_owed()
        mock_client = MagicMock()
        mock_client.send_payout.side_effect = RuntimeError("payout network down")
        record = engine.check_auto_payout(wallet, [], payout_client=mock_client)
        assert record is not None
        assert record.completed is False
        assert record.error is not None
        assert wallet.owner_owed == Decimal("50.00")

    def test_uses_owner_owed_pool(self):
        """The payout must debit the owner_owed bucket, not the reinvest pool."""
        engine = self._engine()
        wallet = Wallet(free=Decimal("999.00"), owner_owed=Decimal("25.00"))
        mock_client = MagicMock()
        mock_client.send_payout.return_value = (PayoutStatus.SENT, "ok")
        record = engine.check_auto_payout(wallet, [], payout_client=mock_client)
        assert record is not None and record.completed
        assert wallet.owner_owed == Decimal("0.00")
        assert wallet.free == Decimal("999.00")  # reinvest capital untouched
        # The withdrawal pool must have been OWNER_OWED — check on the client
        # call args isn't possible directly (client only sees withdrawal_id +
        # amount), so assert via the pre-deduction behavior above.

    def test_manual_queue_marks_complete(self):
        """QUEUED_MANUAL (no Payoneer creds) is a recorded, completed payout."""
        engine = self._engine()
        wallet = self._wallet_with_owed()
        mock_client = MagicMock()
        mock_client.send_payout.return_value = (PayoutStatus.QUEUED_MANUAL, "not configured")
        record = engine.check_auto_payout(wallet, [], payout_client=mock_client)
        assert record is not None
        assert record.completed is True
        assert wallet.owner_owed == Decimal("0.00")
        assert engine.policy.seed_capital_repaid == Decimal("50.00")

    def test_roundtrip_with_process_withdrawal(self, monkeypatch):
        """End-to-end: process_withdrawal properly handles OWNER_OWED pool."""
        from src.withdrawal import PayoutStatus, process_withdrawal

        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        monkeypatch.delenv("PAYONEER_PROGRAM_ID", raising=False)

        wallet = Wallet(free=Decimal("10.00"), owner_owed=Decimal("15.00"))
        result = process_withdrawal(wallet, WithdrawalPool.OWNER_OWED, Decimal("15.00"))
        assert wallet.owner_owed == Decimal("0.00")
        assert wallet.free == Decimal("10.00")
        assert result.payout_status is PayoutStatus.QUEUED_MANUAL


# ---------------------------------------------------------------------------
# UI / status helpers
# ---------------------------------------------------------------------------

class TestGetCurrentTier:
    def test_active_tier_output(self):
        engine = RevenueSplitEngine(policy=_policy_with_tiers())
        info = engine.get_current_tier(Decimal("250.00"))
        assert info["free_balance"] == "250.00"
        assert info["tier_fractions"]["owner"] == "0.3"
        assert info["tier_fractions"]["reinvest"] == "0.5"
        assert info["tier_fractions"]["reserve"] == "0.2"