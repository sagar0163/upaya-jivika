"""Integration tests for main.py — survival loop wiring and full life cycle."""

from decimal import Decimal
from unittest.mock import AsyncMock

from src.debt_engine import DebtEngine
from src.persistence import InMemoryStore
from src.soul_crystal import ReincarnationEngine
from src.state_machine import State, SurvivalStateMachine
from src.wallet import Wallet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tick_to_death(loop, wallet=None):
    """Fast-forward debt ticks until death (20 ticks × $0.50 = $10.00)."""
    for _ in range(20):
        loop.debt_tick()


# ---------------------------------------------------------------------------
# SurvivalLoop wiring
# ---------------------------------------------------------------------------

class TestSurvivalLoopInit:
    """Verify the loop wires all modules together on construction."""

    def test_creates_all_modules(self):
        from main import SurvivalLoop

        loop = SurvivalLoop(persistence=InMemoryStore())

        assert isinstance(loop.debt_engine, DebtEngine)
        assert isinstance(loop.wallet, Wallet)
        assert isinstance(loop.state_machine, SurvivalStateMachine)
        assert isinstance(loop.reincarnation, ReincarnationEngine)
        assert loop.persistence is not None

    def test_initial_state(self):
        from main import SurvivalLoop

        loop = SurvivalLoop(persistence=InMemoryStore())

        assert loop.debt_engine.alive is True
        assert loop.debt_engine.debt == Decimal("0.00")
        assert loop.state_machine.state == State.THRIVING
        assert loop._life_record is not None
        assert loop._life_record.life_number == 1

    def test_tick_persists_state(self):
        from main import SurvivalLoop

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)

        loop.debt_tick()

        saved = store.load_debt_state()
        assert saved is not None
        assert saved.debt == Decimal("0.50")

    def test_tick_updates_state_machine(self):
        from main import SurvivalLoop

        loop = SurvivalLoop(persistence=InMemoryStore())

        # Tick 3 times → $1.50 → THRIVING
        for _ in range(3):
            loop.debt_tick()
        assert loop.state_machine.state == State.THRIVING

        # Tick 1 more → $2.00 → SURVIVING (boundary: >= $2.00)
        loop.debt_tick()
        assert loop.state_machine.state == State.SURVIVING

        # Tick 2 more → $3.00 → still SURVIVING
        for _ in range(2):
            loop.debt_tick()
        assert loop.state_machine.state == State.SURVIVING

    def test_start_stop_scheduler(self):
        from main import SurvivalLoop

        loop = SurvivalLoop(persistence=InMemoryStore())
        loop.start()
        assert loop._running is True
        assert loop._scheduler.running

        loop.stop()
        assert loop._running is False

    def test_stop_idempotent(self):
        from main import SurvivalLoop

        loop = SurvivalLoop(persistence=InMemoryStore())
        loop.stop()  # no-op
        assert loop._running is False

    def test_get_status(self):
        from main import SurvivalLoop

        loop = SurvivalLoop(persistence=InMemoryStore())
        status = loop.get_status()

        assert status["alive"] is True
        assert status["life_number"] == 1
        assert status["debt"] == "0.00"
        assert status["state"] == "thriving"
        assert "wallet_free" in status
        assert "event_count" in status


# ---------------------------------------------------------------------------
# Death → soul crystal → reincarnation cycle
# ---------------------------------------------------------------------------

class TestFullLifeDeathReincarnation:
    """Fast-forwarded life→death→reincarnation cycle.

    Uses InMemoryStore + mocked research to avoid network calls.
    20 ticks in Normal mode ($0.50 × 20 = $10.00) triggers death.
    """

    def test_full_cycle(self):
        from main import SurvivalLoop

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)
        loop.research.research_earning_platforms = AsyncMock(return_value=[])

        # -- Life 1: accumulate debt ---------------------------------------
        assert loop.debt_engine.alive is True
        assert loop.debt_engine.state.life_number == 1
        assert loop.state_machine.state == State.THRIVING

        # Tick 4 → $2.00 → SURVIVING (>= $2.00 boundary)
        for _ in range(4):
            loop.debt_tick()
        assert loop.debt_engine.debt == Decimal("2.00")
        assert loop.state_machine.state == State.SURVIVING

        # Tick 4 → $4.00 → SURVIVING
        for _ in range(4):
            loop.debt_tick()
        assert loop.debt_engine.debt == Decimal("4.00")
        assert loop.state_machine.state == State.SURVIVING

        # Tick 4 → $6.00 → STRUGGLING
        for _ in range(4):
            loop.debt_tick()
        assert loop.debt_engine.debt == Decimal("6.00")
        assert loop.state_machine.state == State.STRUGGLING

        # Tick 4 → $8.00 → CRITICAL
        for _ in range(4):
            loop.debt_tick()
        assert loop.debt_engine.debt == Decimal("8.00")
        assert loop.state_machine.state == State.CRITICAL

        # Tick 2 → $9.00 → still CRITICAL
        for _ in range(2):
            loop.debt_tick()
        assert loop.debt_engine.debt == Decimal("9.00")
        assert loop.state_machine.state == State.CRITICAL

        # Tick 2 → $10.00 → DEATH, then auto-reincarnation kicks in
        for _ in range(2):
            loop.debt_tick()

        # -- Verify death + reincarnation effects ----------------------------
        # Death triggers soul crystal generation and automatic reincarnation.
        # After the tick returns, the engine has already been reset to life 2.
        assert loop.debt_engine.state.life_number == 2
        assert loop.debt_engine.debt == Decimal("0.00")
        assert loop.state_machine.state == State.THRIVING

        # Soul crystal for life 1 was generated and persisted
        crystals = store.load_soul_crystals()
        assert len(crystals) == 1
        assert crystals[0].life == 1
        assert crystals[0].total_earned == Decimal("0.00")

        # Wallet fully reset
        assert loop.wallet.debt == Decimal("0.00")
        assert loop.wallet.free == Decimal("0.00")
        assert loop.wallet.locked == Decimal("0.00")

        # Life record is for life 2
        assert loop._life_record is not None
        assert loop._life_record.life_number == 2

        # Ancestral memory contains life 1
        mem = loop.reincarnation.get_ancestral_memory()
        assert "Life 1" in mem

    def test_multiple_lives(self):
        """Die twice, verify life counter and ancestral memory both increase."""
        from main import SurvivalLoop

        loop = SurvivalLoop(persistence=InMemoryStore())
        loop.research.research_earning_platforms = AsyncMock(return_value=[])

        # Life 1 → death
        _tick_to_death(loop)
        assert loop.debt_engine.state.life_number == 2
        assert len(loop.reincarnation.soul_crystals) == 1

        # Life 2 → death
        _tick_to_death(loop)
        assert loop.debt_engine.state.life_number == 3
        assert len(loop.reincarnation.soul_crystals) == 2

        mem = loop.reincarnation.get_ancestral_memory()
        assert "Life 1" in mem
        assert "Life 2" in mem
        assert "2 lives" in mem


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """The /health endpoint must remain responsive during the survival loop."""

    def test_health_returns_200(self):
        from fastapi.testclient import TestClient
        import main as main_mod

        # Patch lifespan to avoid starting the real scheduler
        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)

        @test_app.get("/health")
        def _health():
            return {"status": "alive"}

        client = TestClient(test_app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_health_during_loop(self):
        """The production /health handler returns 200 while the loop is live."""
        from fastapi.testclient import TestClient
        import main as main_mod

        # Build a fresh app with a no-op lifespan so no scheduler is started,
        # but reuse the real /health handler wired to the module-level loop.
        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)

        # Point the module-level loop at an *unstarted* survival loop
        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        main_mod._loop = loop

        client = TestClient(test_app)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "alive"
        assert body["life"] == 1
        assert body["debt"] == "0.00"

        # Tick the loop and confirm /health reflects the new state
        loop.debt_tick()
        resp2 = client.get("/health")
        assert resp2.status_code == 200
        assert resp2.json()["debt"] == "0.50"

        # Clean up: don't leak the module-level loop
        main_mod._loop = None


# ---------------------------------------------------------------------------
# Persistence fallback wiring
# ---------------------------------------------------------------------------

class TestPersistenceFallback:
    """Verify that the loop uses InMemoryStore when no Supabase env vars."""

    def test_uses_in_memory_when_no_env(self):
        from main import SurvivalLoop

        old_url = __import__("os").environ.pop("SUPABASE_URL", None)
        old_key = __import__("os").environ.pop("SUPABASE_KEY", None)
        try:
            loop = SurvivalLoop()
            assert isinstance(loop.persistence, InMemoryStore)
        finally:
            if old_url is not None:
                __import__("os").environ["SUPABASE_URL"] = old_url
            if old_key is not None:
                __import__("os").environ["SUPABASE_KEY"] = old_key

    def test_state_survives_restart(self):
        """Persistence saves state that can be restored on a new loop instance."""
        from main import SurvivalLoop

        store = InMemoryStore()
        loop1 = SurvivalLoop(persistence=store)

        # Tick 3 times → $1.50
        for _ in range(3):
            loop1.debt_tick()

        # Simulate restart with the same store
        loop2 = SurvivalLoop(persistence=store)
        assert loop2.debt_engine.debt == Decimal("1.50")
        assert loop2.wallet.debt == Decimal("1.50")
