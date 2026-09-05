"""Survival loop entrypoint — wires all modules into a running FastAPI service.

Uses APScheduler for the debt tick (24 h) and research trigger (6 h).
Runs the survival loop in the background via a lifespan hook so the web
service stays responsive while the loop runs.

The ``/health`` endpoint is always available.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from src.alert_system import AlertLevel, AlertSystem
from src.ancestral_memory import AncestralMemory, load_ancestral_memory
from src.approval_gate import ApprovalGate, SpendDecision
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
from src.research_loop import ResearchAgent
from src.respawn_policy import RespawnPolicyEngine
from src.scam_detection import ScamEvent, ScamTracker
from src.soul_crystal import LifeRecord, ReincarnationEngine
from src.state_machine import SurvivalStateMachine
from src.wallet import SpendRequest, Wallet, WalletError
from src.withdrawal import WithdrawalError, WithdrawalPool, process_withdrawal

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

        # Wire persistence on every debt tick
        self.debt_engine._on_tick = self._on_tick  # type: ignore[assignment]

        # Wire death callback
        self.debt_engine._on_death = self._on_death  # type: ignore[assignment]

        # Internal state
        self._life_record: LifeRecord | None = None
        self._event_log: list[str] = []
        self._running = False
        self.ancestral_memory: AncestralMemory | None = None

        # Restore persisted state
        self._restore_state()

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
        life_num = self.reincarnation.next_life_number()
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

        # Generate soul crystal
        crystal = self.reincarnation.on_death(state.debt)
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
            logger.info("Research cycle complete: %d topics", len(results))
            if self.ws_manager:
                status = self.get_status()
                status["event"] = "research_cycle"
                await self.ws_manager.broadcast(status)
        except Exception:
            logger.exception("Research cycle failed")

    def survival_tick(self) -> None:
        """Periodic state-machine sync (runs every minute)."""
        if not self.debt_engine.alive:
            return
        self.state_machine.update(self.debt_engine.debt)
        self.wallet.debt = self.debt_engine.debt
        self.resolve_pending_spends()

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
        if self.persistence.is_payment_processed(event.payment_id):
            logger.info("Payoneer payment %s already processed — skipping", event.payment_id)
            return {"processed": False, "reason": "duplicate", "payment_id": event.payment_id}

        if not event.is_completed:
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
        self._scheduler.add_job(
            self.scan_email_for_payment_alerts, "interval", minutes=15, id="email_scan"
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
            "respawn_policy": self.respawn.policy.value,
            "task_knowledge_entries": len(self.respawn),
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

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def dashboard():
    """Serve the live survival dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
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


# ---------------------------------------------------------------------------
# API endpoints for GitHub Actions cron jobs
# ---------------------------------------------------------------------------


@app.post("/api/debt/tick")
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


@app.post("/api/research/trigger")
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

    result = loop.record_payment(event)
    return result


@app.get("/api/email/status")
async def email_status_endpoint():
    """Report whether the email inbox is configured (diagnostic only)."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    return {"configured": loop.email_inbox.is_configured}


@app.post("/api/email/scan")
async def email_scan_endpoint():
    """Manually trigger a payment-alert email scan (also runs every 15 min)."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    return {"alerts_found": loop.scan_email_for_payment_alerts()}


@app.get("/api/spend/pending")
async def pending_spends_endpoint():
    """List AI spend requests currently held in their veto window."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    return {"pending": [p.to_dict() for p in loop.approval_gate.list_pending()]}


@app.post("/api/spend/{spend_id}/reject")
async def reject_spend_endpoint(spend_id: str):
    """User vetoes a pending AI spend before its window elapses."""
    loop = _loop
    if loop is None:
        raise HTTPException(status_code=503, detail="Survival loop not initialised")
    rejected = loop.reject_pending_spend(spend_id)
    if not rejected:
        raise HTTPException(status_code=404, detail="No pending (unexpired) spend with that id")
    return {"rejected": True, "spend_id": spend_id}


@app.post("/api/withdraw")
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
        return loop.process_withdrawal(pool, amount)
    except (WithdrawalError, WalletError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
