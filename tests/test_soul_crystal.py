"""Unit tests for soul_crystal.py — reincarnation logic."""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from src.soul_crystal import (
    SoulCrystal,
    DeathLog,
    LifeRecord,
    ReincarnationEngine,
    generate_soul_crystal,
    generate_death_log,
    build_ancestral_memory,
)


class TestSoulCrystalGeneration:
    def test_crystal_from_record(self):
        record = LifeRecord(
            life_number=1,
            born_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            total_earned=Decimal("3.20"),
            peak_state="surviving",
            best_platform="Clickworker",
            events=["Earned $1.50 on Day 3", "Debt exceeded $10.00"],
        )
        crystal = generate_soul_crystal(record)
        assert crystal.life == 1
        assert crystal.total_earned == Decimal("3.20")
        assert crystal.peak_state == "surviving"
        assert crystal.best_platform == "Clickworker"
        assert crystal.cause_of_death != ""

    def test_crystal_includes_failures(self):
        record = LifeRecord(
            life_number=2,
            born_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            failed_strategies=["Fiverr writing - rejected gigs"],
            avoid=["tasks taking >2 days"],
        )
        crystal = generate_soul_crystal(record)
        assert "Fiverr writing - rejected gigs" in crystal.failed_strategies
        assert "tasks taking >2 days" in crystal.avoid


class TestDeathLog:
    def test_death_log_generation(self):
        record = LifeRecord(
            life_number=1,
            born_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            total_earned=Decimal("2.00"),
            peak_state="thriving",
            events=["Started life 1"],
        )
        log = generate_death_log(record, final_debt=Decimal("10.00"))
        assert log.life == 1
        assert log.final_debt == Decimal("10.00")
        assert log.total_earned == Decimal("2.00")
        assert len(log.events) > 0


class TestAncestralMemory:
    def test_empty_crystals(self):
        assert build_ancestral_memory([]) == ""

    def test_single_crystal(self):
        crystal = SoulCrystal(
            life=1,
            born=datetime(2026, 9, 1),
            died=datetime(2026, 9, 21, tzinfo=timezone.utc),
            lifespan_days=20,
            total_earned=Decimal("3.20"),
            peak_state="surviving",
            best_platform="Clickworker",
            cause_of_death="Debt exceeded $10.00",
            key_lessons=["Data annotation pays faster"],
            failed_strategies=["Fiverr writing - rejected gigs"],
            avoid=["tasks taking >2 days"],
        )
        mem = build_ancestral_memory([crystal])
        assert "Life 1" in mem
        assert "Clickworker" in mem
        assert "Data annotation pays faster" in mem
        assert "Fiverr writing - rejected gigs" in mem
        assert "tasks taking >2 days" in mem

    def test_multiple_crystals_compressed(self):
        crystals = [
            SoulCrystal(
                life=i,
                born=datetime(2026, 9, 1),
                died=datetime(2026, 9, 21, tzinfo=timezone.utc),
                lifespan_days=20,
                total_earned=Decimal("1.00"),
                key_lessons=[f"Lesson from life {i}"],
            )
            for i in range(1, 4)
        ]
        mem = build_ancestral_memory(crystals)
        assert "Life 1" in mem
        assert "Life 2" in mem
        assert "Life 3" in mem
        assert "3 lives" in mem


class TestReincarnationEngine:
    def test_full_lifecycle(self):
        engine = ReincarnationEngine()

        # Life 1
        rec = engine.start_new_life(1)
        rec.record_earning(Decimal("2.50"), "Toloka")
        rec.record_event("First earnings!")
        crystal = engine.on_death(Decimal("10.00"))
        assert crystal.life == 1
        assert crystal.total_earned == Decimal("2.50")

        # Life 2
        assert engine.next_life_number() == 2
        rec2 = engine.start_new_life(2)
        rec2.record_earning(Decimal("4.00"), "Clickworker")
        crystal2 = engine.on_death(Decimal("10.00"))
        assert crystal2.life == 2

        # Ancestral memory should have both lives
        mem = engine.get_ancestral_memory()
        assert "Life 1" in mem
        assert "Life 2" in mem
        assert "2 lives" in mem

    def test_next_life_number_increments(self):
        engine = ReincarnationEngine()
        assert engine.next_life_number() == 1
        engine.start_new_life(1)
        engine.on_death(Decimal("10.00"))
        assert engine.next_life_number() == 2

    def test_death_without_active_life_raises(self):
        engine = ReincarnationEngine()
        with pytest.raises(RuntimeError):
            engine.on_death(Decimal("10.00"))
