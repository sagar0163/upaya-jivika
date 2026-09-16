"""Survival loop entrypoint — wires all modules into a running FastAPI service.

Uses APScheduler for the debt tick (24 h) and research trigger (6 h).
Runs the survival loop in the background via a lifespan hook so the web
service stays responsive while the loop runs.

The ``/health`` endpoint is always available.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager

# Issue #63: Optional Survival Mode toggle
# SURVIVAL_MODE=0 → normal earning mode without reincarnation.
# SURVIVAL_MODE=1 (default) → survival/reincarnation framework active.
# The env var is only the *boot-time default*; once the operator toggles the
# mode via /api/survival-mode it is persisted in app_settings and wins on
# every later startup.
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from src.alert_system import AlertLevel, AlertSystem
from src.ancestral_memory import AncestralMemory, load_ancestral_memory
from src.api_auth import SESSION_COOKIE, extract_presented_token, require_api_token
from src.approval_gate import ApprovalGate, SpendDecision
from src.audit_trail import AuditTrail
from src.captcha_handler import BotDetectionTracker
from src.cold_archive import ColdArchive
from src.debt_engine import DebtEngine, DebtState, DifficultyMode
from src.diary import DiaryWriter
from src.email_inbox import EmailInboxClient, is_payment_alert
from src.payoneer_webhook import (
    SIGNATURE_HEADER,
    PayoneerWebhookError,
    PayoneerWebhookEvent,
    parse_webhook_payload,
    verify_signature,
)
from src.persistence import PersistenceStore, create_persistence_store
from src.research_loop import ResearchAgent, persist_research_scores
from src.respawn_policy import RespawnPolicyEngine
from src.scam_detection import ScamEvent, ScamTracker, ScamType
from src.soul_crystal import (
    LifeRecord,
    ReincarnationEngine,
    build_carry_over_research_scores,
)
from src.state_machine import SurvivalStateMachine, min_certainty
from src.task_executor import TaskExecutor
from src.task_scorer import Platform as EarningPlatform
from src.vault import assert_vault_security_ready, get_vault
from src.wallet import SpendRequest, Wallet, WalletError
from src.withdrawal import WithdrawalError, WithdrawalPool, process_withdrawal

# §7 Single-Platform MVP (issue #62): focus on ONE platform before expanding.
# Starting with Clickworker (India-supported, 70% certainty per artifact.md §7).
# ONLY after this works, add second platform per the executor rule: one working
# platform beats 20 broken ones.
EARNING_PLATFORMS = [EarningPlatform.CLICKWORKER]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket Manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict[str, Any]):
        disconnected = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        for d in disconnected:
            self.active_connections.discard(d)

ws_manager = ConnectionManager()


#: Custom WebSocket close codes for unauthenticated handshakes (issue #74).
#: RFC 6455 reserves 1000-4999 for applications; 4401/4403 mirror the HTTP
#: 401/403 semantics the rest of the surface uses. The connection is accepted
#: and closed with one of these before any state is sent (see
#: ``_reject_websocket``).
WS_CLOSE_MISSING_TOKEN = 4401
WS_CLOSE_INVALID_TOKEN = 4403


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

    def __init__(
        self,
        persistence: PersistenceStore | None = None,
        ws_mgr: ConnectionManager | None = ws_manager,
    ) -> None:
        self.persistence = persistence or create_persistence_store()
        self.ws_manager = ws_mgr
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None

        # Core modules
        self.debt_engine = DebtEngine(mode=DifficultyMode.NORMAL)
        self.wallet = Wallet()
        self.state_machine = SurvivalStateMachine()
        self.reincarnation = ReincarnationEngine()
        self.research = ResearchAgent()
        self.diary = DiaryWriter()
        self.cold_archive = ColdArchive()
        self.alerts = AlertSystem()
        self.respawn = RespawnPolicyEngine()
        self.scam_tracker = ScamTracker(self.persistence)
        self.email_inbox = EmailInboxClient()
        self.approval_gate = ApprovalGate(self.persistence)
        self.bot_tracker = BotDetectionTracker(self.persistence)
        self.audit_trail = AuditTrail()
        self.task_executor = TaskExecutor(
            wallet=self.wallet,
            vault=get_vault(),
            scam_tracker=self.scam_tracker,
            bot_tracker=self.bot_tracker,
            audit_trail=self.audit_trail,
        )

        # Wire persistence on every debt tick
        self.debt_engine._on_tick = self._on_tick  # type: ignore[assignment]

        # Wire death callback
        self.debt_engine._on_death = self._on_death  # type: ignore[assignment]

        # Internal state
        self._life_record: LifeRecord | None = None
        self._event_log: list[str] = []
        self._running = False
        self.ancestral_memory: AncestralMemory | None = None

        # Issue #63: survival mode is an operator-facing, persisted toggle.
        # The env var is the boot-time default; once set via the API/UI it is
        # stored in app_settings and survives restarts. The engine and respawn
        # machinery are always constructed (a mid-life toggle must not leave
        # None where a future reincarnation would dereference), but every
        # death-time decision gates on self._survival_mode.
        persisted_mode = self.persistence.load_survival_mode()
        self._survival_mode = (
            persisted_mode
            if persisted_mode is not None
            else os.environ.get("SURVIVAL_MODE", "1") == "1"
        )
        if self._survival_mode:
            logger.info(
                "Survival mode enabled (env default=%s) — reincarnation with "
                "ancestral carry-over is active",
                os.environ.get("SURVIVAL_MODE", "1"),
            )
        else:
            logger.info(
                "Survival mode disabled — operating in normal earning mode "
                "without reincarnation / ancestral memory carry-over"
            )

        # Restore persisted state
        self._restore_state()

    def set_survival_mode(self, enabled: bool) -> None:
        """Toggle survival mode at runtime (issue #63).

        Applies from the next death onward and is persisted to ``app_settings``
        so the choice survives restarts. Reincarnation/respawn machinery stays
        constructed either way, so a mid-life toggle never leaves the loop in a
        half-initialised state.
        """
        enabled = bool(enabled)
        self._survival_mode = enabled
        self.persistence.save_survival_mode(enabled)
        logger.info("Survival mode set to %s (persisted)", enabled)
        self._event_log.append(f"Survival mode {'enabled' if enabled else 'disabled'}")
        self._persist_all()
        self._broadcast_event("survival_mode")

    def _broadcast_event(self, event_name: str) -> None:
        if self.ws_manager:
            status = self.get_status()
            status["event"] = event_name
            if self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(self.ws_manager.broadcast(status), self._event_loop)
            else:
                try:
                    # Fallback for synchronous test environments without a running event loop
                    asyncio.run(self.ws_manager.broadcast(status))
                except RuntimeError:
                    pass

    # -- persistence --------------------------------------------------------

    def _restore_state(self) -> None:
        """Restore hot-memory state from persistence on startup."""
        debt_state = self.persistence.load_debt_state()
        wallet_data = self.persistence.load_wallet()
        life_record = self.persistence.load_life_record()

        # The core hot-memory entities (debt_state, wallet, life_record) are
        # written together as a coherent unit by _persist_all. If only *some*
        # of them survived (e.g. a write failed partway, or the process died
        # between saves), resuming would resurrect the agent in a torn state —
        # alive with no wallet. Treat a partial set as corrupt and start fresh
        # rather than restoring an inconsistent half-snapshot.
        present = {
            "debt_state": debt_state is not None,
            "wallet": wallet_data is not None,
            "life_record": life_record is not None,
        }
        if any(present.values()) and not all(present.values()):
            logger.warning(
                "Inconsistent persisted snapshot (%s) — treating as partial "
                "write and starting a fresh life",
                present,
            )
            # Load the permanent archive first so the fresh life inherits the
            # correct next life number and ancestral memory.
            self.reincarnation.soul_crystals = self.persistence.load_soul_crystals()
            self._start_fresh_life()
            return

        if debt_state:
            self.debt_engine.restore(debt_state)
            logger.info(
                "Restored debt state: life=%s debt=$%s alive=%s",
                debt_state.life_number,
                debt_state.debt,
                debt_state.alive,
            )

        if wallet_data:
            self.wallet = Wallet(
                locked=Decimal(wallet_data["locked"]),
                free=Decimal(wallet_data["free"]),
                debt=Decimal(wallet_data["debt"]),
            )
            logger.info("Restored wallet: $%s total", self.wallet.total_balance)

        if life_record:
            self._life_record = life_record
            logger.info("Restored life record: life %d", life_record.life_number)

        self._event_log = self.persistence.load_events()

        # Load past soul crystals into reincarnation engine
        crystals = self.persistence.load_soul_crystals()
        self.reincarnation.soul_crystals = crystals

        # If no life record exists yet, start life 1
        if self._life_record is None:
            self._start_fresh_life()

    def _start_fresh_life(self) -> None:
        """Begin life 1 (or the next life) with clean hot-memory state."""
        if self._survival_mode:
            life_num = self.reincarnation.next_life_number()
        else:
            # Issue #63: with the toggle off this is a classic single-life agent —
            # always life 1, and the in-memory crystal archive is not used as
            # ancestral-carry-over (the persisted §10 archive is preserved for
            # if/when the operator re-enables survival mode).
            life_num = 1
            self.reincarnation.soul_crystals = []
        self.debt_engine.reset_for_new_life(life_num)
        self.state_machine.reset()
        self.wallet = Wallet()
        self._life_record = self.reincarnation.start_new_life(life_num)
        self._event_log = [f"Life {life_num} born"]
        self._persist_all()
        self.cold_archive.begin_life(life_num)
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

        # Layer 3 cold archive — every debt tick survives hot-memory wipe
        self.cold_archive.append_event(
            "debt_tick", {"debt": str(debt), "state": self.state_machine.state.value}
        )

        self._persist_all()
        self._broadcast_event("debt_tick")

        if transition:
            self._event_log.append(
                f"State: {transition.previous.value} → {transition.current.value}"
            )
            self.cold_archive.append_event(
                "state_transition",
                {"from": transition.previous.value, "to": transition.current.value},
            )
            logger.info(
                "State transition: %s → %s (debt $%s)",
                transition.previous.value,
                transition.current.value,
                debt,
            )
            self._broadcast_event("state_transition")

            # Raise an alert when the agent first enters a danger state
            alert = self.alerts.on_state_change(
                previous=transition.previous.value,
                current=transition.current.value,
                debt=debt,
            )
            if alert:
                logger.warning(
                    "ALERT [%s] %s", alert.level.value.upper(), alert.message
                )

        # Write daily diary entry
        try:
            self.diary.on_tick(
                life_number=self.debt_engine.state.life_number,
                debt=debt,
                state=self.state_machine.state.value,
                events=list(self._event_log),
            )
        except Exception:
            logger.exception("Diary write failed on tick")

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
        self.cold_archive.append_event(
            "death", {"debt": str(state.debt), "life_number": state.life_number}
        )

        # Raise a terminal alert so the user knows the agent has died
        alert = self.alerts.on_death(debt=state.debt, life_number=state.life_number)
        if alert:
            logger.warning("ALERT [%s] %s", alert.level.value.upper(), alert.message)

        if self._survival_mode:
            # Generate soul crystal and reincarnate
            # Generate soul crystal — pass this life's research scores so the
            # crystal captures only THIS life's top-3 platform certainties /
            # task affinities as its ancestral carry-over (issue #63), rather
            # than aggregating the inherited seed into its own lessons.
            research_scores = self.persistence.load_research_scores()
            crystal = self.reincarnation.on_death(state.debt, research_scores)
            self.persistence.save_soul_crystal(crystal)

            # Persist final death state
            self._persist_all()

            logger.info("Soul crystal generated for life %d", state.life_number)
            self._broadcast_event("death")

            # Write death note + soul crystal to GitHub diary
            try:
                self.diary.on_death(
                    life_number=state.life_number,
                    final_debt=state.debt,
                    total_earned=self._life_record.total_earned if self._life_record else Decimal("0"),
                    peak_state=self._life_record.peak_state if self._life_record else "thriving",
                    best_platform=self._life_record.best_platform if self._life_record else "",
                    events=list(self._event_log),
                    failed_strategies=self._life_record.failed_strategies if self._life_record else [],
                    key_lessons=self._life_record.events if self._life_record else [],
                    avoid=self._life_record.avoid if self._life_record else [],
                    soul_crystal=crystal,
                )
            except Exception:
                logger.exception("Diary write failed on death")

            # --- Reincarnation ---
            # Flush remaining events before switching life in the archive
            self.cold_archive.flush()
            self._reincarnate(state)
        else:
            # Survival mode off — just end the agent, no reincarnation
            logger.info("Survival mode off — agent ending, no reincarnation")
            self._event_log.append(
                f"END: debt ${state.debt}, life {state.life_number} "
                "(survival mode off, no reincarnation)"
            )
            self._persist_all()
            self._broadcast_event("death")
            # Do NOT reincarnate - agent just ends

    def _reincarnate(self, state: DebtState) -> None:
        """Reset hot-memory state for a new life.

        Soul crystals (permanent memory, §10 Layer 2/3) survive the wipe —
        ``clear()`` preserves the archive by contract; only hot-memory state
        (wallet, task queue, events, life record) is reset here.
        """
        logger.info("Reincarnating — resetting hot-memory state")
        self._event_log.append("REINCARNATION")

        # Reset modules
        new_life_num = self.reincarnation.next_life_number()
        self.debt_engine.reset_for_new_life(new_life_num)
        self.state_machine.reset()
        self.wallet = Wallet()
        self.alerts.reset()
        self.respawn.on_reincarnate()
        self._life_record = self.reincarnation.start_new_life(new_life_num)
        self._event_log = [f"Life {new_life_num} born"]

        # Begin the new life in the cold archive (new JSONL shard)
        self.cold_archive.reset_for_new_life()
        self.cold_archive.begin_life(new_life_num)

        # Wipe hot-memory state; the permanent soul-crystal archive is
        # preserved by clear() (and lives on in the engine's memory).
        self.persistence.clear()

        # Issue #63 ancestral carry-over: the dying life's research scores are
        # wiped and the new life is re-seeded from the top-3 platform
        # certainties / task affinities that THIS life's research distilled
        # into its soul crystal. Bounded to 3 per axis, so a reborn agent
        # inherits what past lives found most certain without god-mode: fresh
        # research in the new life still dominates the seed as it accrues.
        self.persistence.clear_research_scores()
        for score in build_carry_over_research_scores(self.reincarnation.soul_crystals):
            self.persistence.save_research_score(score)

        # Load ancestral memory — compress all past soul crystals
        # into a bounded block (never blocks a new life from starting)
        try:
            self.ancestral_memory = load_ancestral_memory(
                new_life_num, self.persistence
            )
        except Exception:
            logger.exception("Ancestral memory load failed")
            self.ancestral_memory = AncestralMemory(generation=new_life_num)

        # Persist fresh hot state
        self._persist_all()
        logger.info("New life %d started", new_life_num)
        self._broadcast_event("reincarnation")

        # Write born tag to GitHub diary
        try:
            self.diary.reset_day_counter()
            self.diary.on_tick(
                life_number=new_life_num,
                debt=Decimal("0.00"),
                state=self.state_machine.state.value,
                events=list(self._event_log),
            )
        except Exception:
            logger.exception("Diary write failed on rebirth")

    # -- scheduler jobs -----------------------------------------------------

    def debt_tick(self) -> Decimal:
        """Fire a single debt tick."""
        return self.debt_engine.tick_now()

    async def research_trigger(self) -> None:
        """Run the research cycle asynchronously."""
        try:
            logger.info("Research cycle starting")
            results = await self.research.research_earning_platforms()
            persisted = persist_research_scores(results, self.persistence)
            logger.info(
                "Research cycle complete: %d topics, %d certainties persisted",
                len(results),
                len(persisted),
            )
            for r in results:
                self._event_log.append(
                    f"Research: {r.topic.value} (confidence {r.confidence:.2f})"
                )
                self.cold_archive.append_event(
                    "research",
                    {
                        "topic": r.topic.value,
                        "confidence": r.confidence,
                        "summary": r.summary,
                    },
                )
            self._persist_all()
            if self.ws_manager:
                status = self.get_status()
                status["event"] = "research_cycle"
                await self.ws_manager.broadcast(status)
        except Exception:
            logger.exception("Research cycle failed")

    async def earning_cycle(self) -> list[dict[str, Any]]:
        """Discover → score → execute real tasks via TaskExecutor (artifact.md §7/§14).

        The only entry point that turns research/wallet bookkeeping into an
        actual attempt at earning money. Silently does nothing per-platform
        when that platform has no credentials configured yet (TaskExecutor's
        existing soft-dependency behavior) — this stays safe to run on a
        schedule even before any platform account is set up.
        """
        results: list[dict[str, Any]] = []
        if not self.debt_engine.alive:
            return results

        # __init__ constructs task_executor before _restore_state() may replace
        # self.wallet with a freshly-restored instance — keep them in sync.
        self.task_executor.wallet = self.wallet

        # Starting TaskExecutor launches a real headless Chromium process —
        # non-trivial memory on a free-tier dyno. Skip it entirely until at
        # least one platform actually has credentials in the vault, so this
        # scheduled job stays a no-op (no browser, no memory cost) rather than
        # paying that cost with nothing to log into.
        vault = self.task_executor._vault
        has_credentials = vault is not None and any(
            vault.get_password(p.value) for p in EARNING_PLATFORMS
        )
        if not has_credentials:
            logger.debug("Earning cycle skipped — no platform credentials configured yet")
            return results

        try:
            if not self.task_executor._running:
                await self.task_executor.start()

            threshold = min_certainty(self.wallet.debt)
            task_results = await self.task_executor.run_earning_cycle(
                EARNING_PLATFORMS, self.wallet.debt, threshold
            )

            for result in task_results:
                self.record_task_outcome(
                    platform=result.candidate.platform.value,
                    task_type=result.candidate.task_type.value,
                    success=result.success,
                    amount_earned=result.amount_earned,
                    time_spent_hours=result.time_spent_hours,
                )
                entry = {
                    "task_id": result.task_id,
                    "platform": result.candidate.platform.value,
                    "success": result.success,
                    "amount_earned": str(result.amount_earned),
                }
                results.append(entry)
                self._event_log.append(
                    f"Task {result.task_id} on {result.candidate.platform.value}: "
                    f"{'earned $' + str(result.amount_earned) if result.success else result.error}"
                )

            if results:
                self.cold_archive.append_event("earning_cycle", {"results": results})
                self._persist_all()
                self._broadcast_event("earning_cycle")

            logger.info("Earning cycle complete: %d task(s) attempted", len(results))
        except Exception:
            logger.exception("Earning cycle failed")

        return results

    def survival_tick(self) -> None:
        """Periodic state-machine sync (runs every minute)."""
        if not self.debt_engine.alive:
            return
        self.state_machine.update(self.debt_engine.debt)
        self.wallet.debt = self.debt_engine.debt
        try:
            self.resolve_pending_spends()
        except Exception:
            logger.exception("resolve_pending_spends failed — will retry next tick")
        try:
            self.check_scam_windows()
        except Exception:
            logger.exception("check_scam_windows failed — will retry next tick")

    def record_task_outcome(
        self,
        *,
        platform: str,
        task_type: str,
        success: bool,
        amount_earned: Decimal = Decimal("0"),
        time_spent_hours: Decimal = Decimal("0"),
    ) -> None:
        """Record one empirical task outcome into respawn policy knowledge.

        Call this wherever a task-execution result is produced so the reborn
        agent inherits (or deliberately forgets) what actually paid.
        """
        self.respawn.record_outcome(
            platform=platform,
            task_type=task_type,
            success=success,
            amount_earned=amount_earned,
            time_spent_hours=time_spent_hours,
        )

    # -- payments (§20 payment confirmation) ---------------------------------

    def record_payment(self, event: PayoneerWebhookEvent) -> dict[str, Any]:
        """Process a verified, parsed Payoneer payment event.

        Idempotent by ``payment_id`` — a redelivered webhook (Payoneer retries
        on non-2xx, and a load balancer can duplicate delivery) never credits
        the wallet twice. Only a :attr:`PaymentStatus.COMPLETED` event credits
        the wallet; ``pending``/``failed``/``unknown`` events are recorded for
        the audit trail but do not move money.
        """
        if not event.is_completed:
            # Non-completed statuses aren't claimed/reserved — they carry no
            # money and Payoneer may send several for the same payment_id as
            # its status progresses, so each is just appended to the archive.
            if self.persistence.is_payment_processed(event.payment_id):
                logger.info("Payoneer payment %s already processed — skipping", event.payment_id)
                return {"processed": False, "reason": "duplicate", "payment_id": event.payment_id}
            logger.info(
                "Payoneer payment %s has status=%s — not crediting yet",
                event.payment_id,
                event.status.value,
            )
            self.cold_archive.append_event(
                "payment_status",
                {"payment_id": event.payment_id, "status": event.status.value, "amount": str(event.amount)},
            )
            return {"processed": False, "reason": f"status={event.status.value}", "payment_id": event.payment_id}

        # try_claim_payment is the atomic check-and-reserve: two concurrent
        # deliveries of the same completed-payment webhook (Payoneer retries,
        # or a duplicating load balancer) must only let one of them through
        # to credit_earned — a plain is_payment_processed() check followed by
        # a later mark_payment_processed() leaves a race window where both
        # requests pass the check before either marks it processed.
        if not self.persistence.try_claim_payment(event.payment_id):
            logger.info("Payoneer payment %s already processed — skipping", event.payment_id)
            return {"processed": False, "reason": "duplicate", "payment_id": event.payment_id}

        breakdown = self.wallet.credit_earned(event.amount)
        self.persistence.mark_payment_processed(
            event.payment_id,
            {
                "amount": str(event.amount),
                "currency": event.currency,
                "status": event.status.value,
                "debt_repaid": str(breakdown["debt_repaid"]),
                "to_free": str(breakdown["to_free"]),
            },
        )
        self._event_log.append(
            f"Payment confirmed: {event.payment_id} ${event.amount} "
            f"(debt_repaid=${breakdown['debt_repaid']}, to_free=${breakdown['to_free']})"
        )
        self.cold_archive.append_event(
            "payment_confirmed",
            {
                "payment_id": event.payment_id,
                "amount": str(event.amount),
                "currency": event.currency,
                "debt_repaid": str(breakdown["debt_repaid"]),
                "to_free": str(breakdown["to_free"]),
            },
        )
        self._persist_all()
        self._broadcast_event("payment_confirmed")
        logger.info(
            "Payoneer payment %s confirmed: $%s credited (debt_repaid=$%s, to_free=$%s)",
            event.payment_id,
            event.amount,
            breakdown["debt_repaid"],
            breakdown["to_free"],
        )

        try:
            self.diary.on_tick(
                life_number=self.debt_engine.state.life_number,
                debt=self.debt_engine.debt,
                state=self.state_machine.state.value,
                events=list(self._event_log),
            )
        except Exception:
            logger.exception("Diary write failed on payment confirmation")

        return {
            "processed": True,
            "payment_id": event.payment_id,
            "amount": str(event.amount),
            "debt_repaid": str(breakdown["debt_repaid"]),
            "to_free": str(breakdown["to_free"]),
        }

    # -- scam handling (§20) -------------------------------------------------

    def record_scam(self, event: ScamEvent) -> dict[str, Any]:
        """Process a confirmed scam: permanent blacklist + wallet reversal.

        Called after the (external) research step confirms a suspected scam
        is real, not a legitimate payment delay. A chargeback additionally
        reverses the earlier wallet credit — money already spent or repaid
        toward debt reappears as debt, since it was never really earned.
        """
        self.scam_tracker.record_scam(event)

        reversal: dict[str, Decimal] | None = None
        if event.scam_type.value == "chargeback" and event.amount_lost > 0:
            reversal = self.scam_tracker.resolve_chargeback(self.wallet, event.amount_lost)

        self._event_log.append(
            f"Scam confirmed: {event.platform} ({event.scam_type.value}) — {event.lesson or 'no lesson recorded'}"
        )
        self.cold_archive.append_event("scam_confirmed", event.to_dict())
        self._persist_all()
        self._broadcast_event("scam_confirmed")
        logger.warning(
            "Scam confirmed on %s (%s) — platform permanently blacklisted",
            event.platform,
            event.scam_type.value,
        )

        try:
            self.diary.on_tick(
                life_number=self.debt_engine.state.life_number,
                debt=self.debt_engine.debt,
                state=self.state_machine.state.value,
                events=list(self._event_log),
            )
        except Exception:
            logger.exception("Diary write failed on scam confirmation")

        result: dict[str, Any] = {"platform": event.platform, "scam_type": event.scam_type.value}
        if reversal is not None:
            result["wallet_reversal"] = {k: str(v) for k, v in reversal.items()}
        return result

    def check_scam_windows(self) -> list[dict[str, Any]]:
        """Auto-confirm any payment window that exceeded window + grace.

        ``PaymentWindow.is_grace_exceeded`` already documents this exact
        threshold as "treat as confirmed scam per the §20 protocol" — this
        just acts on that contract on a schedule instead of requiring a
        human to notice and call :meth:`record_scam` manually. A window still
        inside its grace period (``overdue_tasks``) is only a suspicion and
        is left alone; only grace-exceeded windows are auto-confirmed.
        """
        results: list[dict[str, Any]] = []
        for window in self.scam_tracker.grace_exceeded_tasks():
            if self.scam_tracker.is_platform_scammed(window.platform):
                self.scam_tracker.mark_paid(window.task_id)
                continue
            event = ScamEvent(
                platform=window.platform,
                scam_type=ScamType.TIME_SCAM,
                life=self.debt_engine.state.life_number,
                lesson=(
                    f"Task {window.task_id} on {window.platform} unpaid "
                    f"past its {window.platform_type.value} grace deadline"
                ),
            )
            results.append(self.record_scam(event))
            self.scam_tracker.mark_paid(window.task_id)
        return results

    # -- email inbox (platform verifications + payment alerts) --------------

    def scan_email_for_payment_alerts(self) -> list[dict[str, Any]]:
        """Scan unread mail for payment-alert messages, log them, mark read.

        This is a *signal*, not a source of truth for crediting the wallet —
        the Payoneer webhook (``record_payment``) remains the only path that
        moves money. An email alert here just surfaces to the diary/event
        feed that a payment appears to have landed, e.g. as a cross-check
        against §20 payment-window monitoring while the webhook may be
        delayed or the platform pays via a channel with no webhook at all.
        Silently returns an empty list when the inbox isn't configured.
        """
        found: list[dict[str, Any]] = []
        try:
            messages = self.email_inbox.fetch_unread()
        except Exception:
            logger.exception("Email inbox scan failed")
            return found

        for msg in messages:
            if not is_payment_alert(msg):
                continue
            entry = {"sender": msg.sender, "subject": msg.subject}
            found.append(entry)
            self._event_log.append(f"Payment alert email: {msg.subject} (from {msg.sender})")
            self.cold_archive.append_event("payment_alert_email", entry)
            try:
                self.email_inbox.mark_as_read(msg.uid)
            except Exception:
                logger.exception("Failed to mark payment-alert email %s as read", msg.uid)

            haystack = f"{msg.sender} {msg.subject}".lower()
            for platform in self.scam_tracker.unpaid_platforms():
                if platform.lower() in haystack:
                    resolved = self.scam_tracker.mark_platform_paid(platform)
                    if resolved:
                        logger.info(
                            "Payment alert matched platform %s — resolved %d window(s)",
                            platform,
                            resolved,
                        )

        if found:
            self._persist_all()
            self._broadcast_event("payment_alert_email")

        return found

    # -- withdrawal (user moves wallet pools to a real bank account) --------

    def process_withdrawal(self, pool: WithdrawalPool, amount: Decimal) -> dict[str, Any]:
        """User-initiated withdrawal from ``pool`` to the user's Payoneer account.

        Only ever called from the user-facing dashboard/API — the AI has no
        code path that reaches this. Debits the wallet immediately; the
        actual bank transfer is attempted via :mod:`src.withdrawal`'s
        Payoneer client and may be ``queued_manual`` if payout credentials
        aren't configured.
        """
        result = process_withdrawal(self.wallet, pool, amount)
        self._event_log.append(
            f"Withdrawal: {result.pool.value} pool -${result.amount} "
            f"(payout={result.payout_status.value})"
        )
        self.cold_archive.append_event("withdrawal", result.to_dict())
        self._persist_all()
        self._broadcast_event("withdrawal")
        logger.info(
            "Withdrawal %s: $%s from %s pool (payout=%s)",
            result.withdrawal_id,
            result.amount,
            result.pool.value,
            result.payout_status.value,
        )
        return result.to_dict()

    # -- human approval gate (§14 — AI free-pool spending) -------------------

    def request_ai_spend(self, amount: Decimal, certainty: Decimal, reason: str) -> dict[str, Any]:
        """The AI's entry point for spending from the free pool.

        Small spends still execute immediately through ``Wallet.ai_spend``'s
        existing debt/certainty/fraction gates. Spends at or above the veto
        threshold are held for a window instead — the user is alerted and
        can reject it; if nobody responds, ``resolve_pending_spends`` (run
        every survival tick) auto-approves it once the window elapses. The
        AI never blocks waiting on a human either way.
        """
        decision, pending = self.approval_gate.request_spend(amount, certainty, reason)

        if decision is SpendDecision.EXECUTED_IMMEDIATELY:
            debited = self.wallet.ai_spend(SpendRequest(amount=amount, certainty=certainty))
            self._event_log.append(f"AI spend executed: ${debited} — {reason}")
            self._persist_all()
            return {"status": decision.value, "amount": str(debited), "reason": reason}

        assert pending is not None  # PENDING always returns a PendingSpend
        self._event_log.append(
            f"AI spend request pending veto (deadline {pending.veto_deadline.isoformat()}): "
            f"${amount} — {reason}"
        )
        self.cold_archive.append_event("spend_request_pending", pending.to_dict())
        self.alerts.raise_alert(
            level=AlertLevel.WARNING,
            state=self.state_machine.state.value,
            message=f"AI wants to spend ${amount} — {reason}. Reject within the veto window to block it.",
            debt=self.debt_engine.debt,
            context=pending.to_dict(),
        )
        self._persist_all()
        self._broadcast_event("spend_request_pending")
        return {
            "status": decision.value,
            "spend_id": pending.spend_id,
            "veto_deadline": pending.veto_deadline.isoformat(),
        }

    def reject_pending_spend(self, spend_id: str) -> bool:
        """User vetoes a pending AI spend before its window elapses."""
        rejected = self.approval_gate.reject(spend_id)
        if rejected:
            self._event_log.append(f"AI spend request {spend_id} rejected by user")
            self._persist_all()
            self._broadcast_event("spend_request_rejected")
        return rejected

    def resolve_pending_spends(self) -> list[dict[str, Any]]:
        """Resolve every pending spend whose veto window has elapsed.

        Auto-approved spends are executed here (through ``Wallet.ai_spend``,
        so its gates are re-checked against current wallet state); rejected
        ones are just logged. Runs every survival tick.
        """
        results: list[dict[str, Any]] = []
        for pending, decision in self.approval_gate.resolve_due():
            entry: dict[str, Any] = {"spend_id": pending.spend_id, "decision": decision.value}
            if decision is SpendDecision.AUTO_APPROVED:
                try:
                    debited = self.wallet.ai_spend(SpendRequest(amount=pending.amount, certainty=pending.certainty))
                    entry["amount"] = str(debited)
                    self._event_log.append(f"AI spend auto-approved after veto window: ${debited} — {pending.reason}")
                except WalletError as exc:
                    entry["error"] = str(exc)
                    self._event_log.append(f"AI spend {pending.spend_id} auto-approval failed: {exc}")
            else:
                self._event_log.append(f"AI spend {pending.spend_id} was rejected — ${pending.amount} not spent")
            results.append(entry)

        if results:
            self._persist_all()
            self._broadcast_event("spend_requests_resolved")
        return results

    # -- lifecycle ----------------------------------------------------------

    def _schedule_coroutine(
        self, coro: Any, label: str
    ) -> asyncio.Future | None:
        """Safely run an async job from APScheduler's worker thread.

        ``coro`` is handed to the event loop that was live when the app
        started (captured in ``__init__`` via ``get_running_loop``) through
        ``run_coroutine_threadsafe`` — the only safe cross-thread mechanism,
        since it submits to a *running* loop that uvicorn is actually
        driving. If no live loop is available this fails loudly: a silent
        no-op here would mean the earning cycle (the revenue path) never
        fires in production and nobody notices (issue #70).
        """
        loop = self._event_loop
        if loop is None:
            logger.error(
                "%s could not be scheduled — no event loop captured at "
                "startup (was SurvivalLoop constructed outside a running app?)",
                label,
            )
            return None
        if not loop.is_running():
            logger.error(
                "%s could not be scheduled — captured event loop is not running",
                label,
            )
            return None
        future = asyncio.run_coroutine_threadsafe(coro, loop)

        def _log_result(fut: asyncio.Future) -> None:
            if fut.cancelled():
                logger.warning("%s was cancelled before it ran", label)
                return
            exc = fut.exception()
            if exc is not None:
                logger.error("%s completed with an exception: %s", label, exc)

        future.add_done_callback(_log_result)
        return future

    def _trigger_research(self) -> None:
        """APScheduler job body — schedules the async research cycle."""
        self._schedule_coroutine(self.research_trigger(), "Research trigger")

    def _trigger_earning_cycle(self) -> None:
        """APScheduler job body — schedules the async earning cycle."""
        self._schedule_coroutine(self.earning_cycle(), "Earning cycle")

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

        # Research trigger every 6 h — run async via the event loop captured at
        # startup (uvicorn's live loop). BackgroundScheduler runs these jobs on
        # worker threads, where asyncio.get_event_loop() is unusable: Python
        # 3.11+ raises RuntimeError in non-main threads with no set loop, and
        # even a loop it did return is never run by anyone (issue #70). We hand
        # the coroutine to the real loop via run_coroutine_threadsafe instead.
        self._scheduler.add_job(
            self._trigger_research, "interval", hours=6, id="research_trigger"
        )

        # Earning cycle every 2 h — same cross-thread scheduling pattern. This
        # is the only code path in the repo that earns money; if it cannot be
        # scheduled it must fail loudly, never silently no-op.
        self._scheduler.add_job(
            self._trigger_earning_cycle, "interval", hours=2, id="earning_cycle"
        )
        self._scheduler.add_job(
            self.scan_email_for_payment_alerts, "interval", minutes=15, id="email_scan"
        )
        self._scheduler.start()
        logger.info(
            "Survival loop started — debt tick every 24 h, research every 6 h, "
            "earning cycle every 2 h"
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
        carried_platforms: list[str] = []
        carried_tasks: list[str] = []
        if self._survival_mode:
            for score in self.persistence.load_research_scores():
                if score.topic == "ancestral_carry_over":
                    carried_platforms.extend(score.platform_certainties.keys())
                    carried_tasks.extend(score.task_affinities.keys())
        return {
            "alive": self.debt_engine.alive,
            "life_number": self.debt_engine.state.life_number,
            "debt": str(self.debt_engine.debt),
            "state": self.state_machine.state.value,
            "survival_mode": self._survival_mode,
            "wallet_locked": str(self.wallet.locked),
            "wallet_free": str(self.wallet.free),
            "wallet_debt": str(self.wallet.debt),
            "total_earned": (
                str(self._life_record.total_earned) if self._life_record else "0"
            ),
            "event_count": len(self._event_log),
            "soul_crystals": len(self.reincarnation.soul_crystals),
            "respawn_policy": self.respawn.policy.value,
            "task_knowledge_entries": len(self.respawn),
            "ancestral_carry_over": {
                "platforms": sorted(carried_platforms),
                "task_types": sorted(carried_tasks),
            },
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

    # Issue #72: fail closed — a live Supabase vault must never boot without
    # VAULT_ENCRYPTION_KEY (otherwise platform passwords would be stored in
    # plaintext). This raises and aborts startup when it would.
    assert_vault_security_ready()

    _loop = SurvivalLoop()
    _loop.start()

    yield

    _loop.stop()
    _loop = None


app = FastAPI(title="upaya-jivika", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def dashboard():
    """Serve the live survival dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    """Bare liveness probe for uptime monitors (issue #74).

    Deliberately returns no economic state: no debt, life number, survival
    state, last earnings, or research-trigger schedule. Anything with a
    wallet/debt/earnings payload belongs behind ``require_api_token`` (see
    ``/status``). Uptime checks only need to know the process is up.
    """
    if _loop is None:
        return {"status": "initialising"}
    return {"status": "ok"}

@app.get("/status", dependencies=[Depends(require_api_token)])
def status():
    """Return the current survival state (gated — full wallet/debt/life snapshot)."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="initialising")
    return loop.get_status()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Issue #74: the WebSocket pushes the full wallet/debt/life snapshot on
    # every event, so the handshake must carry a valid token — via the
    # httpOnly session cookie (browsers attach it to the same-origin
    # handshake automatically), an Authorization: Bearer header, or a
    # ?token= query param (see extract_presented_token). Fails closed.
    expected = os.environ.get("API_AUTH_TOKEN")
    presented = extract_presented_token(websocket)
    if not expected or not presented:
        await _reject_websocket(websocket, WS_CLOSE_MISSING_TOKEN, "Authentication required")
        return
    if not hmac.compare_digest(presented, expected):
        await _reject_websocket(websocket, WS_CLOSE_INVALID_TOKEN, "Invalid API token")
        return

    await ws_manager.connect(websocket)
    loop = _loop
    if loop is not None:
        await websocket.send_json({"event": "status_snapshot", **loop.get_status()})
    try:
        while True:
            await websocket.receive_text()
            if loop is not None:
                await websocket.send_json(
                    {"event": "status_snapshot", **loop.get_status()}
                )
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


async def _reject_websocket(websocket: WebSocket, code: int, reason: str) -> None:
    """Accept then immediately close an unauthenticated WebSocket handshake.

    A handshake-level HTTP 403 would reach browsers as a generic 1006 close
    code — indistinguishable from "server down" — so the dashboard couldn't
    react to "auth required". Upgrading first delivers a real close frame
    carrying 4401/4403 instead. No state is sent before the close.
    """
    await websocket.accept()
    await websocket.close(code=code, reason=reason)


# ---------------------------------------------------------------------------
# Dashboard session (httpOnly cookie so the token never sits in page-readable
# storage — see src/api_auth.py's SESSION_COOKIE docstring)
# ---------------------------------------------------------------------------


@app.post("/api/session")
async def create_session(request: Request, response: Response):
    """Exchange the API token for an httpOnly session cookie.

    The dashboard calls this once (after prompting the user for the token)
    instead of holding the token itself in JS-readable storage. The cookie
    is what every other mutating endpoint accepts via ``require_api_token``.
    """
    token = os.environ.get("API_AUTH_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="API authentication not configured")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed JSON body") from exc

    presented = payload.get("token", "")
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=403, detail="Invalid API token")

    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=_is_https_request(request),
        samesite="strict",
        path="/",
    )
    return {"ok": True}


def _is_https_request(request: Request) -> bool:
    """True if the client connected over HTTPS.

    Render (and most PaaS setups) terminate TLS at the edge and forward
    plain HTTP to the app, so ``request.url.scheme`` reads "http" even in
    production unless uvicorn is told to trust proxy headers (it isn't
    here) — fall back to the ``X-Forwarded-Proto`` header the edge sets.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


@app.post("/api/session/logout")
async def logout_session(response: Response):
    """Clear the dashboard's session cookie."""
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API endpoints for GitHub Actions cron jobs
# ---------------------------------------------------------------------------


@app.post("/api/debt/tick", dependencies=[Depends(require_api_token)])
async def debt_tick_endpoint():
    """Fire a single debt tick. Idempotent - safe to call multiple times.
    
    Returns the new debt amount and whether death was triggered.
    """
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")

    # Check if a tick was already performed in the last 23 hours (deduplication)
    # This prevents double-charging if both APScheduler and GitHub Actions fire
    debt_state = loop.persistence.load_debt_state()
    if debt_state and debt_state.last_tick_at:
        now = datetime.now(timezone.utc)
        # If last tick was within 23 hours, skip (cron runs daily, APScheduler runs daily)
        if (now - debt_state.last_tick_at) < timedelta(hours=23):
            return {
                "skipped": True,
                "reason": "tick already performed recently",
                "last_tick_at": debt_state.last_tick_at.isoformat(),
                "debt": str(debt_state.debt),
                "alive": debt_state.alive,
            }

    new_debt = loop.debt_tick()

    return {
        "skipped": False,
        "debt": str(new_debt),
        "alive": loop.debt_engine.alive,
        "life_number": loop.debt_engine.state.life_number,
    }


@app.post("/api/research/trigger", dependencies=[Depends(require_api_token)])
async def research_trigger_endpoint():
    """Trigger a research cycle.
    
    Returns the research results summary.
    """
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")

    await loop.research_trigger()

    return {
        "status": "completed",
        "topics_researched": len(loop.research.get_history()),
    }


@app.post("/api/webhooks/payoneer")
async def payoneer_webhook(request: Request):
    """Receive a Payoneer payment notification (artifact.md §20).

    Verifies the ``X-Payoneer-Signature`` header (HMAC-SHA256 over the raw
    body, keyed by ``PAYONEER_WEBHOOK_SECRET``) before touching anything, so
    an attacker who knows this URL cannot fabricate payments. Fails closed:
    if the secret isn't configured, every request is rejected rather than
    silently accepted.
    """
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")

    secret = os.environ.get("PAYONEER_WEBHOOK_SECRET")
    if not secret:
        logger.error("Payoneer webhook received but PAYONEER_WEBHOOK_SECRET is not set")
        raise HTTPException(status_code=503, detail="Webhook not configured")

    raw_body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER, "")
    if not verify_signature(secret, raw_body, signature):
        logger.warning("Payoneer webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed JSON body") from exc

    try:
        event = parse_webhook_payload(payload)
    except PayoneerWebhookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Implement retry logic for webhook failures (e.g. transient DB issues)
    retries = 3
    for attempt in range(retries):
        try:
            result = loop.record_payment(event)
            return result
        except Exception as exc:
            if attempt == retries - 1:
                logger.error("Failed to process webhook after %d attempts: %s", retries, exc)
                raise HTTPException(status_code=500, detail="Internal server error during webhook processing")
            logger.warning("Webhook processing failed, retrying (%d/%d): %s", attempt + 1, retries, exc)
            await asyncio.sleep(1)


@app.post("/api/webhooks/payoneer/manual", dependencies=[Depends(require_api_token)])
async def manual_payoneer_confirmation(request: Request):
    """Fallback manual confirmation API path for when the Payoneer webhook fails.

    Bypasses signature verification but requires the API auth token instead.
    """
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed JSON body") from exc

    try:
        event = parse_webhook_payload(payload)
    except PayoneerWebhookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = loop.record_payment(event)
        return result
    except Exception as exc:
        logger.error("Manual payment confirmation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/email/status", dependencies=[Depends(require_api_token)])
async def email_status_endpoint():
    """Report whether the email inbox is configured (diagnostic only).

    Gated (issue #74) — revealing whether an IMAP inbox is wired up would let
    strangers probe the agent's configured infrastructure for call-and-response
    email fishing.
    """
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    return {"configured": loop.email_inbox.is_configured}


@app.post("/api/email/scan", dependencies=[Depends(require_api_token)])
async def email_scan_endpoint():
    """Manually trigger a payment-alert email scan (also runs every 15 min)."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    alerts = await asyncio.to_thread(loop.scan_email_for_payment_alerts)
    return {"alerts_found": alerts}


@app.get("/api/spend/pending", dependencies=[Depends(require_api_token)])
async def pending_spends_endpoint():
    """List AI spend requests currently held in their veto window.

    Gated (issue #74) — pending spends include the AI's reasons, which leak the
    agent's research/strategy to anyone who can fetch the URL.
    """
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    return {"pending": [p.to_dict() for p in loop.approval_gate.list_pending()]}


@app.post("/api/spend/{spend_id}/reject", dependencies=[Depends(require_api_token)])
async def reject_spend_endpoint(spend_id: str):
    """User vetoes a pending AI spend before its window elapses."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    rejected = loop.reject_pending_spend(spend_id)
    if not rejected:
        raise HTTPException(status_code=404, detail="No pending (unexpired) spend with that id")
    return {"rejected": True, "spend_id": spend_id}


@app.post("/api/withdraw", dependencies=[Depends(require_api_token)])
async def withdraw_endpoint(request: Request):
    """User-initiated withdrawal from a wallet pool to a real bank account.

    Body: ``{"pool": "free"|"locked", "amount": "12.50"}``. This is the only
    caller of ``SurvivalLoop.process_withdrawal`` — the AI itself has no
    access to either pool's withdrawal methods.
    """
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed JSON body") from exc

    try:
        pool = WithdrawalPool(payload.get("pool"))
        amount = Decimal(str(payload["amount"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid withdrawal request: {exc}") from exc

    try:
        return await asyncio.to_thread(loop.process_withdrawal, pool, amount)
    except (WithdrawalError, WalletError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Issue #63 — Optional Survival Mode (operator toggle)
# ---------------------------------------------------------------------------


@app.get("/api/survival-mode", dependencies=[Depends(require_api_token)])
async def get_survival_mode_endpoint() -> dict[str, Any]:
    """Return whether survival / reincarnation mode is currently active."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    return {"enabled": loop._survival_mode}


@app.post("/api/survival-mode", dependencies=[Depends(require_api_token)])
async def set_survival_mode_endpoint(request: Request) -> dict[str, Any]:
    """Toggle survival / reincarnation mode at runtime (issue #63).

    Body: ``{"enabled": true|false}``.

    The change is persisted (survives restarts) and applies from the next
    death onward:
    - ``enabled=true``: a death produces a soul crystal and the agent is
      reincarnated with ancestral carry-over (top-3 platform certainties +
      top-3 task affinities seeded into the new life's research table).
    - ``enabled=false``: the agent stays alive earning normally, but on death
      it ends permanently — no reincarnation, no carry-over.
    """
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed JSON body") from exc
    if not isinstance(payload, dict) or "enabled" not in payload:
        raise HTTPException(status_code=400, detail="Body must be {\"enabled\": bool}")
    enabled = payload["enabled"]
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="\"enabled\" must be a boolean")

    loop.set_survival_mode(enabled)
    return {"enabled": loop._survival_mode, "persisted": True}
