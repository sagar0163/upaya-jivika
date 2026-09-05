"""Tests for src/withdrawal.py — withdrawal mechanism (artifact.md critical gap)."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.wallet import Wallet, WalletError
from src.withdrawal import (
    PayoneerPayoutClient,
    PayoutStatus,
    WithdrawalError,
    WithdrawalPool,
    process_withdrawal,
)


class TestPayoneerPayoutClientUnconfigured:
    def test_not_configured_without_env(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        monkeypatch.delenv("PAYONEER_PROGRAM_ID", raising=False)
        client = PayoneerPayoutClient()
        assert client.is_configured is False

    def test_unconfigured_queues_manual(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        monkeypatch.delenv("PAYONEER_PROGRAM_ID", raising=False)
        client = PayoneerPayoutClient()
        status, detail = client.send_payout("w1", Decimal("10.00"))
        assert status is PayoutStatus.QUEUED_MANUAL
        assert "not configured" in detail


class TestPayoneerPayoutClientConfigured:
    def test_configured_sends_payout(self, monkeypatch):
        monkeypatch.setenv("PAYONEER_API_KEY", "key")
        monkeypatch.setenv("PAYONEER_PROGRAM_ID", "prog")
        mock_http = MagicMock()
        mock_http.post.return_value.raise_for_status.return_value = None
        client = PayoneerPayoutClient(http_client=mock_http)

        status, detail = client.send_payout("w1", Decimal("10.00"))

        assert status is PayoutStatus.SENT
        mock_http.post.assert_called_once()
        call_kwargs = mock_http.post.call_args
        assert "prog" in call_kwargs.args[0]
        assert call_kwargs.kwargs["json"]["amount"] == "10.00"

    def test_configured_http_failure_reports_failed(self, monkeypatch):
        monkeypatch.setenv("PAYONEER_API_KEY", "key")
        monkeypatch.setenv("PAYONEER_PROGRAM_ID", "prog")
        mock_http = MagicMock()
        mock_http.post.side_effect = RuntimeError("network down")
        client = PayoneerPayoutClient(http_client=mock_http)

        status, detail = client.send_payout("w1", Decimal("10.00"))

        assert status is PayoutStatus.FAILED
        assert "network down" in detail


class TestProcessWithdrawal:
    def test_withdraws_from_free_pool(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        wallet = Wallet(free=Decimal("20.00"))
        result = process_withdrawal(wallet, WithdrawalPool.FREE, Decimal("5.00"))
        assert wallet.free == Decimal("15.00")
        assert result.pool is WithdrawalPool.FREE
        assert result.amount == Decimal("5.00")
        assert result.payout_status is PayoutStatus.QUEUED_MANUAL

    def test_withdraws_from_locked_pool(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        wallet = Wallet(locked=Decimal("20.00"))
        result = process_withdrawal(wallet, WithdrawalPool.LOCKED, Decimal("5.00"))
        assert wallet.locked == Decimal("15.00")
        assert result.pool is WithdrawalPool.LOCKED

    def test_negative_amount_rejected(self):
        wallet = Wallet(free=Decimal("20.00"))
        with pytest.raises(WithdrawalError):
            process_withdrawal(wallet, WithdrawalPool.FREE, Decimal("-1"))

    def test_zero_amount_rejected(self):
        wallet = Wallet(free=Decimal("20.00"))
        with pytest.raises(WithdrawalError):
            process_withdrawal(wallet, WithdrawalPool.FREE, Decimal("0"))

    def test_insufficient_balance_raises_wallet_error(self):
        wallet = Wallet(free=Decimal("2.00"))
        with pytest.raises(WalletError):
            process_withdrawal(wallet, WithdrawalPool.FREE, Decimal("5.00"))

    def test_uses_provided_payout_client(self):
        wallet = Wallet(free=Decimal("20.00"))
        mock_client = MagicMock()
        mock_client.send_payout.return_value = (PayoutStatus.SENT, "ok")
        result = process_withdrawal(wallet, WithdrawalPool.FREE, Decimal("5.00"), payout_client=mock_client)
        assert result.payout_status is PayoutStatus.SENT
        mock_client.send_payout.assert_called_once()

    def test_withdrawal_id_is_unique(self):
        wallet = Wallet(free=Decimal("20.00"))
        mock_client = MagicMock()
        mock_client.send_payout.return_value = (PayoutStatus.QUEUED_MANUAL, "")
        r1 = process_withdrawal(wallet, WithdrawalPool.FREE, Decimal("1.00"), payout_client=mock_client)
        r2 = process_withdrawal(wallet, WithdrawalPool.FREE, Decimal("1.00"), payout_client=mock_client)
        assert r1.withdrawal_id != r2.withdrawal_id

    def test_wallet_debited_even_if_payout_fails(self):
        wallet = Wallet(free=Decimal("20.00"))
        mock_client = MagicMock()
        mock_client.send_payout.return_value = (PayoutStatus.FAILED, "boom")
        process_withdrawal(wallet, WithdrawalPool.FREE, Decimal("5.00"), payout_client=mock_client)
        assert wallet.free == Decimal("15.00")
