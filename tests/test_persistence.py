"""Unit tests for persistence.py — hot-memory persistence layer."""

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.debt_engine import DebtState, DifficultyMode
from src.persistence import (
    InMemoryStore,
    _debt_state_from_dict,
    _debt_state_to_dict,
    _life_record_from_dict,
    _life_record_to_dict,
    _soul_crystal_from_dict,
    _soul_crystal_to_dict,
    _wallet_from_dict,
    _wallet_to_dict,
    create_persistence_store,
)
from src.soul_crystal import LifeRecord, SoulCrystal
from src.wallet import Wallet

# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

class TestDebtStateSerialization:
    def test_roundtrip(self):
        state = DebtState(
            debt=Decimal("5.50"),
            mode=DifficultyMode.HARD,
            alive=True,
            life_number=3,
            born_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        )
        d = _debt_state_to_dict(state)
        restored = _debt_state_from_dict(d)
        assert restored.debt == Decimal("5.50")
        assert restored.mode == DifficultyMode.HARD
        assert restored.alive is True
        assert restored.life_number == 3


class TestWalletSerialization:
    def test_roundtrip(self):
        w = Wallet(locked=Decimal("10.00"), free=Decimal("5.50"), debt=Decimal("2.00"))
        d = _wallet_to_dict(w)
        assert d == {"locked": "10.00", "free": "5.50", "debt": "2.00"}
        restored = _wallet_from_dict(d, Wallet)
        assert restored.locked == Decimal("10.00")
        assert restored.free == Decimal("5.50")
        assert restored.debt == Decimal("2.00")


class TestLifeRecordSerialization:
    def test_roundtrip(self):
        rec = LifeRecord(
            life_number=2,
            born_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            total_earned=Decimal("4.25"),
            peak_state="surviving",
            events=["event1", "event2"],
            failed_strategies=["fail1"],
            avoid=["avoid1"],
            best_platform="Toloka",
            best_daily_avg=Decimal("0.50"),
        )
        d = _life_record_to_dict(rec)
        restored = _life_record_from_dict(d)
        assert restored.life_number == 2
        assert restored.total_earned == Decimal("4.25")
        assert restored.peak_state == "surviving"
        assert restored.events == ["event1", "event2"]
        assert restored.failed_strategies == ["fail1"]
        assert restored.avoid == ["avoid1"]
        assert restored.best_platform == "Toloka"


class TestSoulCrystalSerialization:
    def test_roundtrip(self):
        crystal = SoulCrystal(
            life=1,
            born=datetime(2026, 9, 1),
            died=datetime(2026, 9, 21, tzinfo=timezone.utc),
            lifespan_days=20.0,
            total_earned=Decimal("3.20"),
            peak_state="surviving",
            best_platform="Clickworker",
            best_daily_avg=Decimal("0.45"),
            failed_strategies=["Fiverr"],
            avoid=["slow tasks"],
            key_lessons=["lesson1"],
            cause_of_death="Debt exceeded $10.00",
        )
        d = _soul_crystal_to_dict(crystal)
        restored = _soul_crystal_from_dict(d)
        assert restored.life == 1
        assert restored.total_earned == Decimal("3.20")
        assert restored.best_platform == "Clickworker"
        assert restored.key_lessons == ["lesson1"]


# ---------------------------------------------------------------------------
# InMemoryStore
# ---------------------------------------------------------------------------

class TestInMemoryStore:
    @pytest.fixture
    def store(self):
        return InMemoryStore()

    def test_debt_state_save_load(self, store):
        state = DebtState(debt=Decimal("3.00"), life_number=1)
        store.save_debt_state(state)
        loaded = store.load_debt_state()
        assert loaded is not None
        assert loaded.debt == Decimal("3.00")
        assert loaded.life_number == 1

    def test_debt_state_load_empty(self, store):
        assert store.load_debt_state() is None

    def test_wallet_save_load(self, store):
        w = Wallet(locked=Decimal("7.00"), free=Decimal("3.00"), debt=Decimal("1.00"))
        store.save_wallet(w)
        loaded = store.load_wallet()
        assert loaded is not None
        assert loaded["locked"] == "7.00"
        assert loaded["free"] == "3.00"
        assert loaded["debt"] == "1.00"

    def test_wallet_load_empty(self, store):
        assert store.load_wallet() is None

    def test_life_record_save_load(self, store):
        rec = LifeRecord(
            life_number=1,
            born_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            total_earned=Decimal("2.50"),
        )
        store.save_life_record(rec)
        loaded = store.load_life_record()
        assert loaded is not None
        assert loaded.life_number == 1
        assert loaded.total_earned == Decimal("2.50")

    def test_life_record_load_empty(self, store):
        assert store.load_life_record() is None

    def test_soul_crystal_save_load(self, store):
        crystal = SoulCrystal(
            life=1,
            born=datetime(2026, 9, 1),
            died=datetime(2026, 9, 21, tzinfo=timezone.utc),
            lifespan_days=20.0,
            total_earned=Decimal("1.50"),
        )
        store.save_soul_crystal(crystal)
        crystals = store.load_soul_crystals()
        assert len(crystals) == 1
        assert crystals[0].life == 1
        assert crystals[0].total_earned == Decimal("1.50")

    def test_multiple_soul_crystals(self, store):
        for i in range(3):
            store.save_soul_crystal(
                SoulCrystal(
                    life=i + 1,
                    born=datetime(2026, 9, 1),
                    died=datetime(2026, 9, 21, tzinfo=timezone.utc),
                    lifespan_days=20.0,
                )
            )
        assert len(store.load_soul_crystals()) == 3

    def test_events_save_load(self, store):
        events = ["tick 1", "tick 2", "death"]
        store.save_events(events)
        loaded = store.load_events()
        assert loaded == ["tick 1", "tick 2", "death"]

    def test_events_save_replaces_not_appends(self, store):
        # _persist_all() re-saves the full growing log on every tick; a
        # second save of a superset must not duplicate the earlier rows.
        store.save_events(["tick 1"])
        store.save_events(["tick 1", "tick 2"])
        store.save_events(["tick 1", "tick 2", "tick 3"])
        assert store.load_events() == ["tick 1", "tick 2", "tick 3"]

    def test_events_save_shrinks_list(self, store):
        # Replacing with a shorter list must drop the stale rows.
        store.save_events(["a", "b", "c"])
        store.save_events(["a"])
        assert store.load_events() == ["a"]

    def test_events_load_empty(self, store):
        assert store.load_events() == []

    def test_clear(self, store):
        store.save_debt_state(DebtState(debt=Decimal("5.00")))
        store.save_wallet(Wallet(free=Decimal("10.00")))
        store.save_life_record(
            LifeRecord(
                life_number=1,
                born_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            )
        )
        store.save_soul_crystal(
            SoulCrystal(
                life=1,
                born=datetime(2026, 9, 1),
                died=datetime(2026, 9, 21, tzinfo=timezone.utc),
                lifespan_days=20.0,
            )
        )
        store.save_events(["event"])

        store.clear()

        assert store.load_debt_state() is None
        assert store.load_wallet() is None
        assert store.load_life_record() is None
        assert store.load_soul_crystals() == []
        assert store.load_events() == []

    def test_save_overwrites_previous(self, store):
        store.save_debt_state(DebtState(debt=Decimal("1.00")))
        store.save_debt_state(DebtState(debt=Decimal("2.00")))
        loaded = store.load_debt_state()
        assert loaded.debt == Decimal("2.00")


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

class TestCreatePersistenceStore:
    def test_in_memory_fallback_when_no_env(self):
        """Without SUPABASE_URL/KEY, factory returns InMemoryStore."""
        old_url = os.environ.pop("SUPABASE_URL", None)
        old_key = os.environ.pop("SUPABASE_KEY", None)
        try:
            store = create_persistence_store()
            assert isinstance(store, InMemoryStore)
        finally:
            if old_url is not None:
                os.environ["SUPABASE_URL"] = old_url
            if old_key is not None:
                os.environ["SUPABASE_KEY"] = old_key

    def test_in_memory_fallback_when_partial_env(self):
        """With only one credential, still falls back to InMemoryStore."""
        old_url = os.environ.pop("SUPABASE_URL", None)
        old_key = os.environ.pop("SUPABASE_KEY", None)
        try:
            os.environ["SUPABASE_URL"] = "https://example.supabase.co"
            # SUPABASE_KEY missing → InMemoryStore
            store = create_persistence_store()
            assert isinstance(store, InMemoryStore)
        finally:
            if old_url is not None:
                os.environ["SUPABASE_URL"] = old_url
            else:
                os.environ.pop("SUPABASE_URL", None)
            if old_key is not None:
                os.environ["SUPABASE_KEY"] = old_key
