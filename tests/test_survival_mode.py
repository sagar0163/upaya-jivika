"""Tests for issue #63 — Optional Survival Mode with Learning Carry-Over.

Covers:
- the persisted operator toggle (persistence + SurvivalLoop + API),
- per-life top-3 capture of platform certainties / task affinities,
- the 3 key lessons / 3 avoided strategies soul-crystal bound,
- warm reseeding of a reborn life's research table from the crystal carry-over,
- survival mode OFF: death ends the agent (no reincarnation, no carry-over).
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

from src.persistence import InMemoryStore, ResearchScore
from src.soul_crystal import (
    LifeRecord,
    SoulCrystal,
    build_carry_over_research_scores,
    carry_over_top3,
    generate_soul_crystal,
    select_avoided,
    select_key_lessons,
)

# ---------------------------------------------------------------------------
# soul_crystal — bounded lessons / avoided / carry-over
# ---------------------------------------------------------------------------

class TestSoulCrystalBounds:
    def test_key_lessons_capped_at_three_newest_outcomes_first(self):
        events = [
            "Debt tick: $0.50",
            "Earned $3.00 on clickworker",
            "Life 1 born",
            "Blocked by upwork",
            "Earned $1.25 on toloka",
            "Debt tick: $0.50",
        ]
        lessons = select_key_lessons(events)
        assert len(lessons) == 3
        # Newest outcome events first (reversed order), bookkeeping excluded.
        assert lessons == [
            "Earned $1.25 on toloka",
            "Blocked by upwork",
            "Earned $3.00 on clickworker",
        ]

    def test_key_lessons_fill_from_recent_events_when_no_outcomes(self):
        lessons = select_key_lessons(
            ["Debt tick: $0.50", "Debt tick: $1.00", "Debt tick: $1.50", "Debt tick: $2.00"]
        )
        assert len(lessons) == 3
        assert lessons == ["Debt tick: $2.00", "Debt tick: $1.50", "Debt tick: $1.00"]

    def test_avoided_capped_at_three(self):
        avoided = select_avoided(["a", "b", "c", "d", "e"])
        assert avoided == ["a", "b", "c"]

    def test_generate_soul_crystal_caps_lessons_and_avoid(self):
        record = LifeRecord(
            life_number=1,
            born_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            events=[f"Debt tick: ${i / 2:.2f}" for i in range(1, 12)]
            + ["Earned $5.00 on clickworker", "Scam detected on xyz"],
            avoid=["a", "b", "c", "d"],
        )
        crystal = generate_soul_crystal(record, research_scores=[])
        assert len(crystal.key_lessons) == 3
        assert len(crystal.avoid) == 3
        assert len(crystal.failed_strategies) <= 3

    def test_crystal_uses_explicit_life_research_scores(self):
        """Issue #63: the crystal must capture THIS life's top-3, not re-read a
        mixed/global research table (which may already include the inherited
        carry-over seed from a past life)."""
        record = LifeRecord(
            life_number=2,
            born_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        scores = [
            ResearchScore(
                topic="earning_platforms",
                query="q",
                findings=[],
                summary="s",
                confidence=0.9,
                sources=[],
                platform_certainties={"clickworker": 0.95, "upwork": 0.4, "prolific": 0.7},
                task_affinities={"microtask": 0.8, "survey": 0.3, "writing": 0.1},
            )
        ]
        crystal = generate_soul_crystal(record, research_scores=scores)
        assert crystal.platform_certainties == [
            ("clickworker", Decimal("0.95")),
            ("prolific", Decimal("0.70")),
            ("upwork", Decimal("0.40")),
        ]
        assert crystal.task_affinities == [
            ("microtask", Decimal("0.80")),
            ("survey", Decimal("0.30")),
            ("writing", Decimal("0.10")),
        ]


class TestCarryOver:
    def test_carry_over_top3_merges_keeping_highest_value(self):
        crystals = [
            SoulCrystal(
                life=1,
                born=datetime(2026, 9, 1, tzinfo=timezone.utc),
                died=datetime(2026, 9, 2, tzinfo=timezone.utc),
                lifespan_days=1,
                platform_certainties=[("clickworker", Decimal("0.95")), ("toloka", Decimal("0.8"))],
                task_affinities=[("microtask", Decimal("0.8")), ("survey", Decimal("0.4"))],
            ),
            SoulCrystal(
                life=2,
                born=datetime(2026, 9, 3, tzinfo=timezone.utc),
                died=datetime(2026, 9, 4, tzinfo=timezone.utc),
                lifespan_days=1,
                platform_certainties=[("prolific", Decimal("0.75")), ("clickworker", Decimal("0.6"))],
                task_affinities=[("writing", Decimal("0.7")), ("microtask", Decimal("0.3"))],
            ),
        ]
        platforms, tasks = carry_over_top3(crystals)
        # clickworker keeps its higher (0.95) across lives; bounded to top 3.
        assert platforms == [
            ("clickworker", Decimal("0.95")),
            ("toloka", Decimal("0.80")),
            ("prolific", Decimal("0.75")),
        ]
        assert tasks == [
            ("microtask", Decimal("0.80")),
            ("writing", Decimal("0.70")),
            ("survey", Decimal("0.40")),
        ]

    def test_carry_over_top3_bounded_to_three(self):
        crystals = [
            SoulCrystal(
                life=1,
                born=datetime(2026, 9, 1, tzinfo=timezone.utc),
                died=datetime(2026, 9, 2, tzinfo=timezone.utc),
                lifespan_days=1,
                platform_certainties=[(f"p{i}", Decimal("0.9")) for i in range(6)],
                task_affinities=[(f"t{i}", Decimal("0.5")) for i in range(6)],
            )
        ]
        platforms, tasks = carry_over_top3(crystals)
        assert len(platforms) == 3
        assert len(tasks) == 3

    def test_build_carry_over_research_scores_empty_without_crystals(self):
        assert build_carry_over_research_scores([]) == []

    def test_build_carry_over_research_scores_seeds_bounded_research(self):
        crystals = [
            SoulCrystal(
                life=1,
                born=datetime(2026, 9, 1, tzinfo=timezone.utc),
                died=datetime(2026, 9, 2, tzinfo=timezone.utc),
                lifespan_days=1,
                platform_certainties=[("clickworker", Decimal("0.95")), ("toloka", Decimal("0.8"))],
                task_affinities=[("microtask", Decimal("0.8"))],
            )
        ]
        seeded = build_carry_over_research_scores(crystals)
        assert len(seeded) == 1
        score = seeded[0]
        assert score.topic == "ancestral_carry_over"
        assert score.platform_certainties == {"clickworker": 0.95, "toloka": 0.8}
        assert score.task_affinities == {"microtask": 0.8}
        # Confidence is the minimum carried certainty — a conservative seed.
        assert score.confidence == 0.8

    def test_build_carry_over_research_scores_round_trips_through_store(self):
        store = InMemoryStore()
        crystals = [
            SoulCrystal(
                life=1,
                born=datetime(2026, 9, 1, tzinfo=timezone.utc),
                died=datetime(2026, 9, 2, tzinfo=timezone.utc),
                lifespan_days=1,
                platform_certainties=[("clickworker", Decimal("0.9"))],
                task_affinities=[("microtask", Decimal("0.6"))],
            )
        ]
        for score in build_carry_over_research_scores(crystals):
            store.save_research_score(score)
        loaded = store.load_research_scores()
        assert len(loaded) == 1
        assert loaded[0].platform_certainties == {"clickworker": 0.9}


# ---------------------------------------------------------------------------
# persistence — fixed toggle row + research wipe
# ---------------------------------------------------------------------------

class TestSurvivalModePersistence:
    def test_round_trip_inmemory(self):
        store = InMemoryStore()
        assert store.load_survival_mode() is None
        store.save_survival_mode(False)
        assert store.load_survival_mode() is False
        store.save_survival_mode(True)
        assert store.load_survival_mode() is True

    def test_survival_mode_survives_hot_memory_clear(self):
        """The mode is an operator setting, not a life's hot memory — a
        reincarnation wipe must not flip it back to the env default."""
        store = InMemoryStore()
        store.save_survival_mode(False)
        store.clear()
        assert store.load_survival_mode() is False

    def test_clear_research_scores(self):
        store = InMemoryStore()
        store.save_research_score(
            ResearchScore(
                topic="earning_platforms",
                query="q",
                findings=[],
                summary="s",
                confidence=0.9,
                sources=[],
                platform_certainties={"clickworker": 0.9},
            )
        )
        assert len(store.load_research_scores()) == 1
        store.clear_research_scores()
        assert store.load_research_scores() == []

    def test_clear_research_scores_preserves_soul_crystals(self):
        store = InMemoryStore()
        store.save_soul_crystal(
            SoulCrystal(
                life=1,
                born=datetime(2026, 9, 1, tzinfo=timezone.utc),
                died=datetime(2026, 9, 2, tzinfo=timezone.utc),
                lifespan_days=1,
            )
        )
        store.clear_research_scores()
        assert len(store.load_soul_crystals()) == 1


# ---------------------------------------------------------------------------
# SurvivalLoop — toggle behaviour end to end
# ---------------------------------------------------------------------------

def _fresh_loop(store: InMemoryStore | None = None):
    from main import SurvivalLoop

    store = store or InMemoryStore()
    loop = SurvivalLoop(persistence=store)
    loop.research.research_earning_platforms = AsyncMock(return_value=[])
    return loop


def _tick_to_death(loop):
    for _ in range(20):
        loop.debt_tick()


class TestSurvivalModeToggle:
    def test_env_default_on(self):
        loop = _fresh_loop()
        assert loop._survival_mode is True
        assert loop.get_status()["survival_mode"] is True

    def test_persisted_off_overrides_env_default_on(self):
        store = InMemoryStore()
        store.save_survival_mode(False)
        loop = _fresh_loop(store)
        assert loop._survival_mode is False

    def test_set_survival_mode_persists_and_visible_in_status(self):
        loop = _fresh_loop()
        loop.set_survival_mode(False)
        assert loop._survival_mode is False
        assert loop.persistence.load_survival_mode() is False
        assert loop.get_status()["survival_mode"] is False
        assert loop.get_status()["life_number"] == 1

    def test_toggle_is_reversible_and_survives_restart(self):
        store = InMemoryStore()
        loop1 = _fresh_loop(store)
        loop1.set_survival_mode(False)

        # Restart: persisted setting wins over the env default.
        loop2 = _fresh_loop(store)
        assert loop2._survival_mode is False

        loop2.set_survival_mode(True)
        loop3 = _fresh_loop(store)
        assert loop3._survival_mode is True


class TestSurvivalModeOff:
    def test_death_ends_agent_without_reincarnation(self):
        store = InMemoryStore()
        store.save_survival_mode(False)
        loop = _fresh_loop(store)

        _tick_to_death(loop)

        # Agent ended: dead, still life 1, no crystal recorded, no reincarnation.
        assert loop.debt_engine.alive is False
        assert loop.debt_engine.state.life_number == 1
        assert store.load_soul_crystals() == []
        assert store.load_research_scores() == []

    def test_off_mode_does_not_carry_over_archive_on_fresh_start(self):
        """Toggling off then restarting on a torn snapshot restarts the agent
        at life 1 with no in-memory ancestral archive (persisted crystals kept)."""
        from main import SurvivalLoop
        from src.debt_engine import DebtState, DifficultyMode
        from src.soul_crystal import SoulCrystal as _SC

        store = InMemoryStore()
        store.save_survival_mode(False)
        store.save_soul_crystal(
            _SC(
                life=3,
                born=datetime(2026, 9, 1, tzinfo=timezone.utc),
                died=datetime(2026, 9, 2, tzinfo=timezone.utc),
                lifespan_days=1,
            )
        )
        # A full snapshot would restore life 3; instead simulate fresh start via
        # a torn snapshot (debt present, wallet + life_record missing).
        store.save_debt_state(
            DebtState(
                debt=Decimal("3.00"),
                mode=DifficultyMode.NORMAL,
                alive=True,
                life_number=3,
            )
        )
        loop = SurvivalLoop(persistence=store)
        assert loop._life_record is not None
        assert loop._life_record.life_number == 1
        assert loop.reincarnation.soul_crystals == []


class TestSurvivalModeCarryOver:
    def test_reincarnation_seeds_research_from_crystal_top3(self):
        """Life 1's research certainties must survive into life 2 as a bounded
        carry-over seed (issue #63)."""
        store = InMemoryStore()
        store.save_research_score(
            ResearchScore(
                topic="earning_platforms",
                query="clickworker",
                findings=[],
                summary="clickworker is most certain",
                confidence=0.95,
                sources=[],
                platform_certainties={
                    "clickworker": 0.95,
                    "toloka": 0.8,
                    "prolific": 0.7,
                    "upwork": 0.4,
                },
                task_affinities={
                    "microtask": 0.8,
                    "survey": 0.5,
                    "writing": 0.3,
                    "dataentry": 0.1,
                },
            )
        )
        loop = _fresh_loop(store)

        _tick_to_death(loop)

        # Reincarnated into life 2.
        assert loop.debt_engine.state.life_number == 2
        assert len(store.load_soul_crystals()) == 1

        # The dying life's full research table was wiped and replaced by only
        # the bounded carry-over seed (topic "ancestral_carry_over").
        research = store.load_research_scores()
        assert len(research) == 1
        assert research[0].topic == "ancestral_carry_over"
        assert research[0].platform_certainties == {
            "clickworker": 0.95,
            "toloka": 0.8,
            "prolific": 0.7,
        }
        assert research[0].task_affinities == {
            "microtask": 0.8,
            "survey": 0.5,
            "writing": 0.3,
        }

        # Observability: status reports the carried platforms/task types.
        status = loop.get_status()
        assert status["ancestral_carry_over"] == {
            "platforms": ["clickworker", "prolific", "toloka"],
            "task_types": ["microtask", "survey", "writing"],
        }

    def test_second_death_keeps_carry_over_bounded(self):
        """Across two deaths the inherited top-3 stays bounded — the lowest
        certainties drop out rather than accumulating into god-mode."""
        store = InMemoryStore()
        store.save_research_score(
            ResearchScore(
                topic="earning_platforms",
                query="q",
                findings=[],
                summary="s",
                confidence=0.9,
                sources=[],
                platform_certainties={f"p{i}": 0.9 - (i * 0.05) for i in range(6)},
                task_affinities={f"t{i}": 0.5 - (i * 0.05) for i in range(6)},
            )
        )
        loop = _fresh_loop(store)

        _tick_to_death(loop)  # life 1 → 2
        _tick_to_death(loop)  # life 2 → 3

        research = store.load_research_scores()
        assert len(research) == 1
        assert len(research[0].platform_certainties) == 3
        assert len(research[0].task_affinities) == 3

    def test_reincarnation_with_no_research_leaves_empty_seed(self):
        """No research this life → no carry-over seed, and no errors."""
        store = InMemoryStore()
        loop = _fresh_loop(store)

        _tick_to_death(loop)

        assert loop.debt_engine.state.life_number == 2
        assert store.load_research_scores() == []


# ---------------------------------------------------------------------------
# API — /api/survival-mode toggle endpoint
# ---------------------------------------------------------------------------

class TestSurvivalModeEndpoint:
    def _client(self, loop):
        from fastapi.testclient import TestClient

        import main as main_mod

        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)
        main_mod._loop = loop
        return TestClient(test_app)

    #: Tests run with API_AUTH_TOKEN=test-token (set in tests/conftest.py).
    _AUTH = {"Authorization": "Bearer test-token"}

    def test_get_returns_current_mode(self):
        loop = _fresh_loop()
        with self._client(loop) as client:
            resp = client.get("/api/survival-mode")
            assert resp.status_code == 200
            assert resp.json()["enabled"] is True

    def test_post_toggles_and_persists(self):
        loop = _fresh_loop()
        with self._client(loop) as client:
            resp = client.post("/api/survival-mode", json={"enabled": False}, headers=self._AUTH)
            assert resp.status_code == 200
            assert resp.json() == {"enabled": False, "persisted": True}
            assert loop._survival_mode is False
            assert loop.persistence.load_survival_mode() is False

    def test_post_requires_auth(self):
        loop = _fresh_loop()
        with self._client(loop) as client:
            assert client.post("/api/survival-mode", json={"enabled": False}).status_code == 401

    def test_post_rejects_missing_boolean(self):
        loop = _fresh_loop()
        with self._client(loop) as client:
            resp = client.post("/api/survival-mode", json={}, headers=self._AUTH)
            assert resp.status_code == 400
            resp2 = client.post(
                "/api/survival-mode", json={"enabled": "notabool"}, headers=self._AUTH
            )
            assert resp2.status_code == 400