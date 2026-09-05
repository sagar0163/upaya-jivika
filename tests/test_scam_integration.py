"""Integration tests for SurvivalLoop.record_scam (§20 scam handling)."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from src.email_inbox import EmailMessage
from src.persistence import InMemoryStore
from src.scam_detection import PaymentWindow, PlatformType, ScamEvent, ScamType
from src.wallet import Wallet


def _loop_with_store():
    import main as main_mod

    store = InMemoryStore()
    loop = main_mod.SurvivalLoop(persistence=store)
    return loop, store


class TestRecordScam:
    def test_time_scam_blacklists_platform(self):
        loop, store = _loop_with_store()
        event = ScamEvent(
            platform="scammy.io",
            scam_type=ScamType.TIME_SCAM,
            life=1,
            hours_wasted=Decimal("8"),
            lesson="No reddit presence = no trust.",
        )

        result = loop.record_scam(event)

        assert result["platform"] == "scammy.io"
        assert store.is_platform_scammed("scammy.io") is True
        assert "wallet_reversal" not in result

    def test_chargeback_reverses_wallet_credit(self):
        loop, _store = _loop_with_store()
        loop.wallet = Wallet(free=Decimal("10.00"))
        event = ScamEvent(
            platform="buyer.io",
            scam_type=ScamType.CHARGEBACK,
            life=1,
            amount_lost=Decimal("4.00"),
        )

        result = loop.record_scam(event)

        assert loop.wallet.free == Decimal("6.00")
        assert result["wallet_reversal"]["from_free"] == "4.00"

    def test_scam_survives_reincarnation_wipe(self):
        loop, store = _loop_with_store()
        loop.record_scam(ScamEvent(platform="scammy.io", scam_type=ScamType.TIME_SCAM, life=1))

        store.clear()

        assert store.is_platform_scammed("scammy.io") is True

    def test_scam_blocks_future_connector_use(self):
        loop, store = _loop_with_store()
        loop.record_scam(ScamEvent(platform="clickworker", scam_type=ScamType.TIME_SCAM, life=1))

        assert loop.scam_tracker.is_platform_scammed("clickworker") is True


class TestCheckScamWindows:
    def test_grace_exceeded_window_auto_confirms_scam(self):
        loop, store = _loop_with_store()
        loop.scam_tracker.register_task(
            PaymentWindow(
                task_id="t1",
                platform="scammy.io",
                platform_type=PlatformType.MICRO_TASK,
                started_at=datetime.utcnow() - timedelta(hours=97),
            )
        )

        results = loop.check_scam_windows()

        assert len(results) == 1
        assert results[0]["platform"] == "scammy.io"
        assert store.is_platform_scammed("scammy.io") is True

    def test_overdue_but_not_grace_exceeded_window_is_left_alone(self):
        loop, store = _loop_with_store()
        loop.scam_tracker.register_task(
            PaymentWindow(
                task_id="t1",
                platform="clickworker",
                platform_type=PlatformType.MICRO_TASK,
                started_at=datetime.utcnow() - timedelta(hours=80),
            )
        )

        results = loop.check_scam_windows()

        assert results == []
        assert store.is_platform_scammed("clickworker") is False

    def test_already_scammed_platform_is_not_recorded_twice(self):
        loop, _store = _loop_with_store()
        loop.record_scam(ScamEvent(platform="clickworker", scam_type=ScamType.TIME_SCAM, life=1))
        loop.scam_tracker.register_task(
            PaymentWindow(
                task_id="t2",
                platform="clickworker",
                platform_type=PlatformType.MICRO_TASK,
                started_at=datetime.utcnow() - timedelta(hours=97),
            )
        )

        results = loop.check_scam_windows()

        assert results == []


class TestEmailPaymentAlertResolvesWindows:
    def test_payment_alert_marks_matching_platform_paid(self):
        loop, _store = _loop_with_store()
        loop.scam_tracker.register_task(
            PaymentWindow(
                task_id="t1",
                platform="clickworker",
                platform_type=PlatformType.MICRO_TASK,
                started_at=datetime.utcnow(),
            )
        )
        alert = EmailMessage(
            uid="1", sender="alerts@clickworker.com", subject="Payout sent", body=""
        )
        mock_inbox = MagicMock()
        mock_inbox.fetch_unread.return_value = [alert]
        loop.email_inbox = mock_inbox

        loop.scan_email_for_payment_alerts()

        assert loop.scam_tracker.unpaid_platforms() == set()

    def test_payment_alert_does_not_touch_unrelated_platform(self):
        loop, _store = _loop_with_store()
        loop.scam_tracker.register_task(
            PaymentWindow(
                task_id="t1",
                platform="toloka",
                platform_type=PlatformType.MICRO_TASK,
                started_at=datetime.utcnow(),
            )
        )
        alert = EmailMessage(
            uid="1", sender="alerts@clickworker.com", subject="Payout sent", body=""
        )
        mock_inbox = MagicMock()
        mock_inbox.fetch_unread.return_value = [alert]
        loop.email_inbox = mock_inbox

        loop.scan_email_for_payment_alerts()

        assert loop.scam_tracker.unpaid_platforms() == {"toloka"}
