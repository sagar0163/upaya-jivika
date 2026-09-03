"""Survival loop entrypoint — wires all modules into a running FastAPI service.

Uses APScheduler for the debt tick (24 h) and research trigger (6 h).
Runs the survival loop in the background via a lifespan hook so the web
service stays responsive while the loop runs.

The ``/health`` endpoint is always available.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException

from src.debt_engine import DebtEngine, DebtState, DifficultyMode
from src.persistence import PersistenceStore, create_persistence_store
from src.research_loop import ResearchAgent
from src.soul_crystal import LifeRecord, ReincarnationEngine
from src.state_machine import SurvivalStateMachine
from src.wallet import Wallet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Survival loop
# ---------------------------------------------------------------------------

class SurvivalLoop:
    """Orchestrates the survival lifecycle:

    - Debt tick (via DebtEngine)
    - State machine sync (debt → survival state)
    - Research trigger (every 6 h / on state change / empty queue)
    - Death → soul crystal → reincarnation → hot-memory wipe
    """

    def __init__(self, persistence: PersistenceStore | None = None) -> None:
        self.persistence = persistence or create_persistence_store()

        # Core modules
        self.debt_engine = DebtEngine(mode=DifficultyMode.NORMAL)
        self.wallet = Wallet()
        self.state_machine = SurvivalStateMachine()
        self.reincarnation = ReincarnationEngine()
        self.research = ResearchAgent()

        # Wire persistence on every debt tick
        self.debt_engine._on_tick = self._on_tick  # type: ignore[assignment]

        # Wire death callback
        self.debt_engine._on_death = self._on_death  # type: ignore[assignment]

        # Internal state
        self._life_record: LifeRecord | None = None
        self._event_log: list[str] = []
        self._running = False

        # Restore persisted state
        self._restore_state()

    # -- persistence --------------------------------------------------------

    def _restore_state(self) -> None:
        """Restore hot-memory state from persistence on startup."""
        debt_state = self.persistence.load_debt_state()
        if debt_state:
            self.debt_engine.restore(debt_state)
            logger.info(
                "Restored debt state: life=%s debt=$%s alive=%s",
                debt_state.life_number,
                debt_state.debt,
                debt_state.alive,
            )

        wallet_data = self.persistence.load_wallet()
        if wallet_data:
            self.wallet = Wallet(
                locked=Decimal(wallet_data["locked"]),
                free=Decimal(wallet_data["free"]),
                debt=Decimal(wallet_data["debt"]),
            )
            logger.info("Restored wallet: $%s total", self.wallet.total_balance)

        life_record = self.persistence.load_life_record()
        if life_record:
            self._life_record = life_record
            logger.info("Restored life record: life %d", life_record.life_number)

        self._event_log = self.persistence.load_events()

        # Load past soul crystals into reincarnation engine
        crystals = self.persistence.load_soul_crystals()
        self.reincarnation.soul_crystals = crystals

        # If no life record exists yet, start life 1
        if self._life_record is None:
            life_num = self.reincarnation.next_life_number()
            self._life_record = self.reincarnation.start_new_life(life_num)
            self._persist_all()
            logger.info("Started new life: %d", life_num)

    def _persist_all(self) -> None:
        """Persist all hot-memory state."""
        self.persistence.save_debt_state(self.debt_engine.snapshot())
        self.persistence.save_wallet(self.wallet)
        if self._life_record:
            self.persistence.save_life_record(self._life_record)
        self.persistence.save_events(self._event_log)

    # -- callbacks ----------------------------------------------------------

    def _on_tick(self, debt: Decimal) -> None:
        """Fired after every debt increment — persist and check state."""
        transition = self.state_machine.update(debt)
        self.wallet.debt = debt
        self._event_log.append(f"Debt tick: ${debt}")
        self._persist_all()

        if transition:
            self._event_log.append(
                f"State: {transition.previous.value} → {transition.current.value}"
            )
            logger.info(
                "State transition: %s → %s (debt $%s)",
                transition.previous.value,
                transition.current.value,
                debt,
            )

    def _on_death(self, state: DebtState) -> None:
        """Fired when debt hits $10 — soul crystal + reincarnation."""
        logger.info(
            "DEATH — life %s, debt $%s. Generating soul crystal...",
            state.life_number,
            state.debt,
        )
        self._event_log.append(
            f"DEATH: debt ${state.debt}, life {state.life_number}"
        )

        # Generate soul crystal
        crystal = self.reincarnation.on_death(state.debt)
        self.persistence.save_soul_crystal(crystal)

        # Persist final death state
        self._persist_all()

        logger.info("Soul crystal generated for life %d", state.life_number)

        # --- Reincarnation ---
        self._reincarnate(state)

    def _reincarnate(self, state: DebtState) -> None:
        """Reset hot-memory state for a new life.

        Soul crystals (permanent memory, §10 Layer 2/3) survive the wipe;
        only hot-memory state (wallet, task queue, events, life record) is reset.
        """
        logger.info("Reincarnating — resetting hot-memory state")
        self._event_log.append("REINCARNATION")

        # Reset modules
        new_life_num = self.reincarnation.next_life_number()
        self.debt_engine.reset_for_new_life(new_life_num)
        self.state_machine.reset()
        self.wallet = Wallet()
        self._life_record = self.reincarnation.start_new_life(new_life_num)
        self._event_log = [f"Life {new_life_num} born"]

        # Wipe hot-memory state, preserving the permanent soul-crystal archive
        self.persistence.clear()
        for crystal in self.reincarnation.soul_crystals:
            self.persistence.save_soul_crystal(crystal)

        # Persist fresh hot state
        self._persist_all()
        logger.info("New life %d started", new_life_num)

    # -- scheduler jobs -----------------------------------------------------

    def debt_tick(self) -> Decimal:
        """Fire a single debt tick."""
        return self.debt_engine.tick_now()

    async def research_trigger(self) -> None:
        """Run the research cycle asynchronously."""
        try:
            logger.info("Research cycle starting")
            results = await self.research.research_earning_platforms()
            for r in results:
                self._event_log.append(
                    f"Research: {r.topic.value} (confidence {r.confidence:.2f})"
                )
            self._persist_all()
            logger.info("Research cycle complete: %d topics", len(results))
        except Exception:
            logger.exception("Research cycle failed")

    def survival_tick(self) -> None:
        """Periodic state-machine sync (runs every minute)."""
        if not self.debt_engine.alive:
            return
        self.state_machine.update(self.debt_engine.debt)
        self.wallet.debt = self.debt_engine.debt

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the survival loop with APScheduler."""
        if self._running:
            return
        self._running = True

        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self.debt_tick, "interval", hours=24, id="debt_tick"
        )
        self._scheduler.add_job(
            self.survival_tick, "interval", minutes=1, id="survival_tick"
        )

        # Research trigger every 6 h — run async via the event loop
        def _trigger_research() -> None:
            try:
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(
                    self.research_trigger(), loop
                )
            except RuntimeError:
                logger.warning("No running event loop for research trigger")

        self._scheduler.add_job(
            _trigger_research, "interval", hours=6, id="research_trigger"
        )
        self._scheduler.start()
        logger.info(
            "Survival loop started — debt tick every 24 h, research every 6 h"
        )

    def stop(self) -> None:
        """Stop the survival loop."""
        if not self._running:
            return
        self._running = False
        if hasattr(self, "_scheduler") and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("Survival loop stopped")

    def get_status(self) -> dict[str, Any]:
        """Return a snapshot of the current survival state."""
        return {
            "alive": self.debt_engine.alive,
            "life_number": self.debt_engine.state.life_number,
            "debt": str(self.debt_engine.debt),
            "state": self.state_machine.state.value,
            "wallet_locked": str(self.wallet.locked),
            "wallet_free": str(self.wallet.free),
            "wallet_debt": str(self.wallet.debt),
            "total_earned": (
                str(self._life_record.total_earned) if self._life_record else "0"
            ),
            "event_count": len(self._event_log),
            "soul_crystals": len(self.reincarnation.soul_crystals),
        }


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

# Module-level instance so tests can import and inspect it.
_loop: SurvivalLoop | None = None


def get_loop() -> SurvivalLoop:
    """Return the module-level SurvivalLoop (set during lifespan startup)."""
    assert _loop is not None, "SurvivalLoop not initialised — is the app running?"
    return _loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the survival loop in the background when the web service boots."""
    global _loop

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _loop = SurvivalLoop()
    _loop.start()

    yield

    _loop.stop()
    _loop = None


app = FastAPI(title="upaya-jivika", lifespan=lifespan)


@app.get("/health")
def health():
    """Health-check — always returns 200 while the service is up."""
    loop = _loop
    if loop is None:
        return {"status": "initialising"}
    return {
        "status": "alive" if loop.debt_engine.alive else "dead",
        "life": loop.debt_engine.state.life_number,
        "debt": str(loop.debt_engine.debt),
        "survival_state": loop.state_machine.state.value,
    }

@app.get("/status")
def status():
    """Return the current survival state."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="initialising")
    return loop.get_status()
