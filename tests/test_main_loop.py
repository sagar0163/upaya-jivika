"""Integration tests for main.py — survival loop wiring and full life cycle."""

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

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

    def test_persistence_keeps_crystal_and_prunes_events_after_reincarnation(self):
        """After death → reincarnation, hot-memory state resets while the
        permanent soul-crystal archive and the per-life event log are both
        correct in persistence (no cross-life bleed, no lost crystals).
        """
        from main import SurvivalLoop

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)
        loop.research.research_earning_platforms = AsyncMock(return_value=[])

        # Accumulate a full life of debt ticks → death → reincarnation.
        _tick_to_death(loop)

        # The new life's hot-memory state is reset...
        assert loop.debt_engine.debt == Decimal("0.00")
        assert loop.debt_engine.state.life_number == 2
        assert loop._event_log == ["Life 2 born"]

        # ...and persisted as such: debt_state/wallet/life_record reflect life 2.
        saved_debt = store.load_debt_state()
        assert saved_debt is not None
        assert saved_debt.debt == Decimal("0.00")
        assert saved_debt.life_number == 2

        saved_life = store.load_life_record()
        assert saved_life is not None
        assert saved_life.life_number == 2

        # Event table is pruned to the current life only — no bleed of the
        # 20+ debt-tick / death events from life 1.
        assert store.load_events() == ["Life 2 born"]

        # The permanent soul-crystal archive survives the wipe and is intact.
        crystals = store.load_soul_crystals()
        assert [c.life for c in crystals] == [1]

    def test_second_reincarnation_does_not_duplicate_crystals(self):
        """Dying twice must archive exactly one crystal per life — the
        preserved archive must never be duplicated by reincarnation."""
        from main import SurvivalLoop

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)
        loop.research.research_earning_platforms = AsyncMock(return_value=[])

        _tick_to_death(loop)  # life 1 → death → life 2
        _tick_to_death(loop)  # life 2 → death → life 3

        assert [c.life for c in store.load_soul_crystals()] == [1, 2]
        assert loop.debt_engine.state.life_number == 3
        assert store.load_events() == ["Life 3 born"]


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
        """/health is a bare liveness probe — public but with no agent state.

        Issue #74: uptime monitors keep a public liveness endpoint, but the
        debt/life/earnings/research-trigger fields move behind auth. The probe
        must never leak economic state.
        """
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
        assert resp.json() == {"status": "ok"}

        # Tick the loop — the probe stays bare even as the agent's state moves.
        loop.debt_tick()
        resp2 = client.get("/health")
        assert resp2.status_code == 200
        body = resp2.json()
        assert body == {"status": "ok"}
        for secret_field in (
            "debt",
            "life",
            "survival_state",
            "last_earnings",
            "next_research_trigger",
        ):
            assert secret_field not in body

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

    def test_partial_snapshot_missing_wallet_starts_fresh(self):
        """A torn snapshot (debt+life present, wallet missing) must not resume.

        Resuming the half-written snapshot would resurrect the agent alive with
        no wallet. The loop must instead fall back to a fresh life.
        """
        from datetime import datetime, timezone

        from main import SurvivalLoop
        from src.debt_engine import DebtState, DifficultyMode
        from src.soul_crystal import LifeRecord

        store = InMemoryStore()
        store.save_debt_state(
            DebtState(
                debt=Decimal("6.00"),
                mode=DifficultyMode.NORMAL,
                alive=True,
                life_number=2,
            )
        )
        store.save_life_record(
            LifeRecord(life_number=2, born_at=datetime.now(timezone.utc))
        )
        # wallet deliberately absent — simulates a partial write

        loop = SurvivalLoop(persistence=store)

        # Must NOT have resumed the torn $6.00 alive snapshot.
        assert loop.debt_engine.debt == Decimal("0.00")
        assert loop.wallet.total_balance == Decimal("0.00")
        assert loop._life_record is not None

    def test_partial_snapshot_missing_debt_state_starts_fresh(self):
        """A torn snapshot (wallet+life present, debt_state missing) must not resume."""
        from datetime import datetime, timezone

        from main import SurvivalLoop
        from src.soul_crystal import LifeRecord

        store = InMemoryStore()
        store.save_wallet(Wallet(locked=Decimal("9.00"), free=Decimal("9.00")))
        store.save_life_record(
            LifeRecord(life_number=5, born_at=datetime.now(timezone.utc))
        )
        # debt_state deliberately absent

        loop = SurvivalLoop(persistence=store)

        assert loop.wallet.total_balance == Decimal("0.00")
        assert loop.debt_engine.debt == Decimal("0.00")
        assert loop._life_record.life_number == 1

    def test_partial_snapshot_inherits_next_life_from_archive(self):
        """A torn snapshot still starts at the next life per the permanent archive."""
        from datetime import datetime, timezone

        from main import SurvivalLoop
        from src.debt_engine import DebtState, DifficultyMode
        from src.soul_crystal import LifeRecord, SoulCrystal

        store = InMemoryStore()
        store.save_debt_state(
            DebtState(
                debt=Decimal("3.00"),
                mode=DifficultyMode.NORMAL,
                alive=True,
                life_number=2,
            )
        )
        store.save_life_record(
            LifeRecord(life_number=2, born_at=datetime.now(timezone.utc))
        )
        # Permanent archive survives — life 1 already crystalised
        store.save_soul_crystal(
            SoulCrystal(
                life=1,
                born=datetime(2026, 9, 1, tzinfo=timezone.utc),
                died=datetime(2026, 9, 21, tzinfo=timezone.utc),
                lifespan_days=20.0,
            )
        )
        # wallet deliberately absent

        loop = SurvivalLoop(persistence=store)

        # Fresh life number derives from the archive: max(1) + 1 = 2
        assert loop.debt_engine.snapshot().life_number == 2
        assert loop.wallet.total_balance == Decimal("0.00")

# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    """The /status endpoint must return the full state — but only to
    authenticated callers (issue #74)."""

    #: Tests run with API_AUTH_TOKEN=test-token (set in tests/conftest.py).
    _AUTH = {"Authorization": "Bearer test-token"}

    def test_status_returns_200_and_keys(self):
        from fastapi.testclient import TestClient

        import main as main_mod

        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        main_mod._loop = loop

        client = TestClient(test_app)
        resp = client.get("/status", headers=self._AUTH)
        
        assert resp.status_code == 200
        body = resp.json()
        assert "alive" in body
        assert "life_number" in body
        assert "debt" in body
        assert "state" in body
        assert "wallet_locked" in body
        assert "wallet_free" in body
        assert "wallet_debt" in body
        assert "total_earned" in body
        assert "event_count" in body
        assert "soul_crystals" in body

        # Test initialising behavior
        main_mod._loop = None
        resp_503 = client.get("/status", headers=self._AUTH)
        assert resp_503.status_code == 503
        assert resp_503.json()["detail"] == "initialising"

    def test_status_rejects_unauthenticated(self):
        """Without credentials /status must be refused — the dashboard prompts
        for auth instead (issue #74)."""
        from fastapi.testclient import TestClient

        import main as main_mod

        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        main_mod._loop = loop

        client = TestClient(test_app)
        assert client.get("/status").status_code == 401
        assert client.get("/status", headers={"Authorization": "Bearer wrong"}).status_code == 403
        main_mod._loop = None


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

class TestSchedulerAsyncWrappers:
    """Issue #70: APScheduler wrapper jobs must execute coroutines on the live
    event loop captured at startup, not silently no-op from a worker thread.

    BackgroundScheduler runs on its own threads. ``asyncio.get_event_loop()``
    fails in non-main threads on Python 3.11+ (RuntimeError), and even when
    it returns a loop that loop is never driven. ``_schedule_coroutine`` uses
    ``run_coroutine_threadsafe`` against the captured live loop instead.
    """

    def _make_loop_in_running_event_loop(self, persistence_store):
        """Construct a SurvivalLoop *inside* a live event loop running in a
        daemon thread, so ``_event_loop`` captures a real running loop."""
        import threading

        import main as main_mod

        holder: dict[str, object] = {}
        started = threading.Event()

        def _drive():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            holder["loop"] = loop
            started.set()
            try:
                loop.run_forever()
            except Exception:
                pass

        thread = threading.Thread(target=_drive, daemon=True)
        thread.start()
        assert started.wait(5), "background loop failed to start"
        loop: asyncio.AbstractEventLoop = holder["loop"]  # type: ignore[assignment]

        async def _construct():
            return main_mod.SurvivalLoop(persistence=persistence_store)

        sl = asyncio.run_coroutine_threadsafe(_construct(), loop).result(timeout=10)
        # cleanup helper
        def _cleanup():
            if hasattr(sl, "_scheduler") and sl._scheduler.running:
                sl._scheduler.shutdown(wait=False)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()

        return sl, loop, _cleanup

    def test_schedule_coroutine_executes_on_live_loop(self):
        """A coroutine submitted via _schedule_coroutine runs and completes on
        the live event loop — not on the APScheduler worker thread."""
        from src.persistence import InMemoryStore

        sl, loop, cleanup = self._make_loop_in_running_event_loop(InMemoryStore())
        try:
            counter = {"n": 0}

            async def _increment():
                await asyncio.sleep(0)  # yields — only completes if loop runs it
                counter["n"] += 1

            future = sl._schedule_coroutine(_increment(), "test")
            assert future is not None
            # Wait for the future to complete (coroutine ran and finished)
            future.result(timeout=5)
            assert counter["n"] == 1
        finally:
            cleanup()

    def test_research_trigger_job_fires_and_executes_coroutine(self):
        """The 'research_trigger' APScheduler job fires its wrapper, which
        schedules research_trigger() onto the live loop and the coroutine runs
        to completion (not just 'scheduled')."""
        from src.persistence import InMemoryStore

        sl, loop, cleanup = self._make_loop_in_running_event_loop(InMemoryStore())
        try:
            executed = {"done": False}

            async def _spy():
                # Only completes if the captured live loop actually drives the
                # coroutine past this await boundary (a dead/unrun loop leaves
                # the counter at 0).
                await asyncio.sleep(0)
                executed["done"] = True

            async def _noop_research():
                return []

            sl.research.research_earning_platforms = _noop_research  # type: ignore[assignment]
            sl.research_trigger = _spy  # type: ignore[assignment]

            sl.start()
            job = sl._scheduler.get_job("research_trigger")
            assert job is not None

            # Simulate the APScheduler worker thread firing the job
            job.func()

            deadline = time.time() + 5
            while not executed["done"] and time.time() < deadline:
                time.sleep(0.01)
            assert executed["done"], "research_trigger coroutine never executed on the live loop"
        finally:
            cleanup()

    def test_earning_cycle_job_fires_and_executes_coroutine(self):
        """The 'earning_cycle' APScheduler job fires its wrapper, which
        schedules the *real* earning_cycle() coroutine onto the live loop and
        it runs to completion: a successful task outcome is recorded into the
        respawn policy, proving money-path code executed on the real loop."""
        from decimal import Decimal

        from src.persistence import InMemoryStore
        from src.task_scorer import PaymentMethod, TaskCandidate, TaskResult, TaskType
        from src.task_scorer import Platform as EarningPlatform
        from src.vault import create_vault

        sl, loop, cleanup = self._make_loop_in_running_event_loop(InMemoryStore())
        try:
            sl.task_executor._vault = create_vault()
            sl.task_executor._vault.set_override("clickworker", "secret")
            sl.task_executor.start = AsyncMock()
            candidate = TaskCandidate(
                platform=EarningPlatform.CLICKWORKER,
                task_type=TaskType.MICROTASK,
                title="Scheduled test task",
                estimated_pay=Decimal("3.00"),
                estimated_hours=Decimal("0.5"),
                payment_method=PaymentMethod.PAYONEER,
            )
            sl.task_executor.run_earning_cycle = AsyncMock(
                return_value=[
                    TaskResult(
                        task_id="sched-1",
                        candidate=candidate,
                        success=True,
                        amount_earned=Decimal("3.00"),
                        time_spent_hours=Decimal("0.5"),
                    )
                ]
            )

            sl.start()
            job = sl._scheduler.get_job("earning_cycle")
            assert job is not None

            # Simulate the APScheduler worker thread firing the job
            job.func()

            # Wait for the real earning_cycle to run to completion on the loop
            deadline = time.time() + 5
            while len(sl.respawn) == 0 and time.time() < deadline:
                time.sleep(0.01)
            assert len(sl.respawn) == 1, (
                "earning_cycle never completed on the live loop — outcome not recorded"
            )
        finally:
            cleanup()

    def test_schedule_coroutine_returns_none_when_no_loop(self):
        """_schedule_coroutine logs an error and returns None when
        _event_loop is None (i.e. loop was constructed outside the app)."""
        from main import SurvivalLoop
        from src.persistence import InMemoryStore

        sl = SurvivalLoop(persistence=InMemoryStore())
        assert sl._event_loop is None  # constructed outside async context

        async def _noop():
            pass

        coro = _noop()
        result = sl._schedule_coroutine(coro, "test no-op")
        coro.close()  # never scheduled → close to avoid a "never awaited" warning
        assert result is None


class TestWebSocketBroadcast:
    """Verify WebSocket broadcasts when loop events occur."""

    @pytest.mark.asyncio
    async def test_websocket_receives_debt_tick(self):
        """A connected client receives a debt_tick broadcast when a tick fires.

        A real server is used so the SurvivalLoop's event loop is the same loop
        that owns the websockets — the broadcast is delivered deterministically
        instead of being raced across two event loops (which made the old
        TestClient-based version flaky).
        """
        import asyncio
        import json
        import threading

        import uvicorn
        import websockets

        import main as main_mod

        holder: dict[str, object] = {}

        @main_mod.asynccontextmanager
        async def _test_lifespan(app):
            holder["loop"] = asyncio.get_running_loop()
            main_mod._loop = main_mod.SurvivalLoop(
                persistence=InMemoryStore()
            )
            yield
            main_mod._loop = None

        test_app = main_mod.FastAPI(title="test", lifespan=_test_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)

        server = uvicorn.Server(
            uvicorn.Config(test_app, host="127.0.0.1", port=0, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        try:
            while not server.started:
                await asyncio.sleep(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]
            # Issue #74: the live feed is gated — connect with a valid token.
            uri = f"ws://127.0.0.1:{port}/ws?token=test-token"

            for _ in range(200):
                if holder.get("loop") is not None:
                    break
                await asyncio.sleep(0.01)
            assert holder["loop"] is not None

            async with websockets.connect(uri) as websocket:
                first = json.loads(await websocket.recv())
                assert first["event"] == "status_snapshot"

                main_mod._loop.debt_tick()

                broadcast = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=5)
                )
                assert broadcast["event"] == "debt_tick"
                assert broadcast["debt"] == "0.50"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            main_mod._loop = None


# ---------------------------------------------------------------------------
# API endpoints for GitHub Actions cron jobs
# ---------------------------------------------------------------------------

class TestDebtTickEndpoint:
    """Test the /api/debt/tick endpoint."""

    def test_debt_tick_endpoint_works(self):
        from fastapi.testclient import TestClient

        import main as main_mod

        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        main_mod._loop = loop

        client = TestClient(test_app)
        resp = client.post("/api/debt/tick", headers={"Authorization": "Bearer test-token"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["skipped"] is False
        assert body["debt"] == "0.50"
        assert body["alive"] is True
        assert body["life_number"] == 1

        main_mod._loop = None

    def test_debt_tick_deduplication(self):
        """Test that ticks within 23 hours are deduplicated."""
        from fastapi.testclient import TestClient

        import main as main_mod

        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        main_mod._loop = loop

        client = TestClient(test_app)

        # First tick
        resp1 = client.post("/api/debt/tick", headers={"Authorization": "Bearer test-token"})
        assert resp1.status_code == 200
        assert resp1.json()["skipped"] is False

        # Second tick immediately - should be deduplicated
        resp2 = client.post("/api/debt/tick", headers={"Authorization": "Bearer test-token"})
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["skipped"] is True
        assert "tick already performed recently" in body2["reason"]
        assert body2["debt"] == "0.50"

        main_mod._loop = None


class TestResearchTriggerEndpoint:
    """Test the /api/research/trigger endpoint."""

    def test_research_trigger_endpoint_works(self):
        from unittest.mock import AsyncMock

        from fastapi.testclient import TestClient

        import main as main_mod

        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        loop.research.research_earning_platforms = AsyncMock(return_value=[])
        main_mod._loop = loop

        client = TestClient(test_app)
        resp = client.post("/api/research/trigger", headers={"Authorization": "Bearer test-token"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert "topics_researched" in body

        main_mod._loop = None
