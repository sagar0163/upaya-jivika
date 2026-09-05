"""Integration tests for SurvivalLoop.record_scam (§20 scam handling)."""

from decimal import Decimal

from src.persistence import InMemoryStore
from src.scam_detection import ScamEvent, ScamType
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
