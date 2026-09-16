"""Human delegation with escrow (issue #76).

When the agent cannot execute a task itself — low scoring certainty, a failed
execution after retries, or work that is inherently human (writing, coding
beyond the agent) — it should be able to *hire a human* rather than quietly
producing a $0 outcome. Competitor research (2026-09): LancerAI hires humans
via Fiverr with escrow; Cooperiano/agora and iAgentPay post human tasks with
auto-release payment; ABB is a human bounty board. This module models the
common primitive, independent of any specific marketplace:

    Commitment  =  (task ref, escrow amount, deadline, verification step)

Rules (deliberately aligned with the existing codebase):

- §14 gates — the escrow is funded through the existing ``Wallet.ai_spend`` +
  :class:`~src.approval_gate.ApprovalGate` path. ``ai_spend`` is the
  permission layer (debt threshold, certainty gate, 30 % free-pool cap);
  ``Wallet.hold_escrow`` then re-tags that debit as *held*, not spent.
- §20 rule 1 — the no-upfront-payment hard rule is absolute. ``release_to_human``
  is the ONLY outward money movement and it raises
  :class:`~src.scam_detection.ScamPreventionError` unless a verification has
  actually passed. Nothing is ever prepaid.
- Escrow release happens only on verified delivery; deadline expiry refunds
  the escrow back to the free pool (``Wallet.escrow_refund``).
- Every transition lands in the audit trail, event feed and cold archive, and
  commitments are persisted in the hot-memory store so they survive restarts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from src.approval_gate import ApprovalGate, SpendDecision
from src.scam_detection import enforce_no_upfront_payment
from src.task_scorer import TaskCandidate, TaskType
from src.wallet import SpendRequest, Wallet, WalletError

logger = logging.getLogger(__name__)

#: Task types the agent cannot reliably execute itself — these are the
#: delegation candidates (design/video/physical work land here in a live
#: deployment; writing/coding are the concrete examples today).
DELEGATION_ELIGIBLE_TYPES: frozenset[TaskType] = frozenset(
    {TaskType.WRITING, TaskType.CODING}
)

#: Fixed per-delegation escrow budget cap, independent of the wallet's 30 %
#: free-pool fraction — a delegation is a small, bounded pilot, not a gamble.
MAX_ESCROW_PER_DELEGATION = Decimal("10.00")

#: Minimum sensible escrow for a human-deliverable task (below this it is not
#: worth a human's time to pick up).
MIN_ESCROW_PER_DELEGATION = Decimal("1.00")

#: Certainty passed into ``ai_spend``/``approval_gate`` for a delegation
#: funding. This is the agent's confidence that a *human* can complete and
#: verify the task (high), NOT the platform certainty that made the automated
#: attempt fail (low) — it is what justifies spending real money.
DELEGATION_FUNDING_CERTAINTY = Decimal("0.95")

#: How long a human has to deliver before the escrow auto-refunds.
DEFAULT_DELEGATION_DEADLINE = timedelta(days=7)


class DelegationChannel(str, Enum):
    """Where the human work is posted. ``MANUAL`` is the marketplace-agnostic
    placeholder: the commitment surfaces on the dashboard/alerts for an
    operator (or a future marketplace bridge) to act on — funding, deadline
    and pay-on-verification semantics are identical either way."""

    MANUAL = "manual"


class CommitmentStatus(str, Enum):
    PENDING = "pending"        # escrow funded; deadline running; awaiting delivery
    DELIVERED = "delivered"    # human claims delivery; awaiting verification
    RELEASED = "released"      # verified; escrow released to the human
    EXPIRED = "expired"        # deadline passed; escrow refunded to free pool
    CANCELLED = "cancelled"    # cancelled; escrow refunded to free pool


class DelegationError(Exception):
    """Raised on illegal delegation operations (bad state transition, etc.)."""


class Commitment(BaseModel):
    """A single human-delegation escrow commitment."""

    commitment_id: str
    source_task_id: str
    task_title: str
    task_description: str = ""
    escrow_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    deadline: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: CommitmentStatus = CommitmentStatus.PENDING
    channel: DelegationChannel = DelegationChannel.MANUAL
    reason: str = ""  # why delegated: "low_certainty" | "executor_failed" | ...
    #: Set when funding waits on the §14 veto window; cleared once funded.
    pending_spend_id: Optional[str] = None
    escrow_funded: bool = False
    verification: str = ""  # evidence recorded at release time
    released_at: Optional[datetime] = None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.status in (CommitmentStatus.PENDING, CommitmentStatus.DELIVERED) and now >= self.deadline

    def is_active(self) -> bool:
        return self.status in (CommitmentStatus.PENDING, CommitmentStatus.DELIVERED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "commitment_id": self.commitment_id,
            "source_task_id": self.source_task_id,
            "task_title": self.task_title,
            "task_description": self.task_description,
            "escrow_amount": str(self.escrow_amount),
            "deadline": self.deadline.isoformat(),
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
            "channel": self.channel.value,
            "reason": self.reason,
            "pending_spend_id": self.pending_spend_id,
            "escrow_funded": self.escrow_funded,
            "verification": self.verification,
            "released_at": self.released_at.isoformat() if self.released_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Commitment":
        return cls(
            commitment_id=d["commitment_id"],
            source_task_id=d.get("source_task_id", ""),
            task_title=d.get("task_title", ""),
            task_description=d.get("task_description", ""),
            escrow_amount=Decimal(d["escrow_amount"]),
            deadline=datetime.fromisoformat(d["deadline"]),
            created_at=(
                datetime.fromisoformat(d["created_at"])
                if d.get("created_at")
                else datetime.now(timezone.utc)
            ),
            status=CommitmentStatus(d.get("status", CommitmentStatus.PENDING.value)),
            channel=DelegationChannel(d.get("channel", DelegationChannel.MANUAL.value)),
            reason=d.get("reason", ""),
            pending_spend_id=d.get("pending_spend_id"),
            escrow_funded=d.get("escrow_funded", False),
            verification=d.get("verification", ""),
            released_at=(
                datetime.fromisoformat(d["released_at"]) if d.get("released_at") else None
            ),
        )


class DelegationResult(BaseModel):
    """Outcome of a ``try_delegate`` call."""

    status: str
    commitment_id: Optional[str] = None
    escrow_amount: Decimal = Decimal("0.00")
    veto_deadline: Optional[str] = None
    reason: str = ""


class DelegationHub:
    """Manages human-delegation commitments and their escrow (issue #76).

    ``wallet``           — the :class:`~src.wallet.Wallet` whose free pool funds the
                           escrow (via ``ai_spend``) and whose escrow pool holds it.
    ``approval_gate``    — the §14 veto-window gate spend requests must pass through.
    ``persistence``      — hot-memory store for the commitment ledger (optional).
    ``audit_trail``      — every transition is recorded for the audit trail (optional).
    ``cold_archive``     — events are appended for Layer 3 survival (optional).
    ``event_sink``       — callable(str) appended to the live event feed (optional).
    """

    def __init__(
        self,
        wallet: Wallet,
        approval_gate: ApprovalGate,
        persistence: Any = None,
        audit_trail: Any = None,
        cold_archive: Any = None,
        event_sink: Optional[Callable[[str], None]] = None,
        deadline: timedelta = DEFAULT_DELEGATION_DEADLINE,
    ) -> None:
        self.wallet = wallet
        self.approval_gate = approval_gate
        self.persistence = persistence
        self.audit_trail = audit_trail
        self.cold_archive = cold_archive
        self.event_sink = event_sink
        self.deadline = deadline
        self._commitments: dict[str, Commitment] = {}
        self._pending_spend_index: dict[str, str] = {}
        self._load()

    # -- internals ----------------------------------------------------------

    def _load(self) -> None:
        if self.persistence is None:
            return
        for data in self.persistence.load_commitments():
            try:
                c = Commitment.from_dict(data)
            except Exception:
                logger.exception("Skipping unparsable commitment record")
                continue
            self._commitments[c.commitment_id] = c
            if c.pending_spend_id:
                self._pending_spend_index[c.pending_spend_id] = c.commitment_id

    def _save(self, c: Commitment) -> None:
        if self.persistence is not None:
            self.persistence.save_commitment(c.commitment_id, c.to_dict())

    def _log(self, message: str) -> None:
        logger.info(message)
        if self.event_sink is not None:
            self.event_sink(message)

    def _cold(self, kind: str, data: dict[str, Any]) -> None:
        if self.cold_archive is not None:
            self.cold_archive.append_event(kind, data)

    def _audit(
        self,
        c: Commitment,
        action: str,
        survival_state: str = "",
        debt: Optional[Decimal] = None,
    ) -> None:
        if self.audit_trail is not None:
            try:
                self.audit_trail.record_delegation(
                    commitment_id=c.commitment_id,
                    source_task_id=c.source_task_id,
                    task_title=c.task_title,
                    action=action,
                    escrow_amount=c.escrow_amount,
                    status=c.status.value,
                    deadline=c.deadline,
                    verification=c.verification,
                    survival_state=survival_state,
                    debt=debt,
                )
            except Exception:
                logger.exception("Delegation audit record failed")

    def _new_commitment(self, candidate: TaskCandidate, amount: Decimal, reason: str) -> Commitment:
        now = datetime.now(timezone.utc)
        return Commitment(
            commitment_id=str(uuid.uuid4()),
            source_task_id=candidate.source_url or f"local:{candidate.title}",
            task_title=candidate.title,
            task_description=candidate.description,
            escrow_amount=amount,
            deadline=now + self.deadline,
            created_at=now,
            reason=reason,
        )

    def _activate_funded(
        self,
        c: Commitment,
        amount: Decimal,
        survival_state: str = "",
        debt: Optional[Decimal] = None,
    ) -> None:
        """Record a funded, PENDING commitment (escrow already held)."""
        c.escrow_funded = True
        c.pending_spend_id = None
        self._commitments[c.commitment_id] = c
        self._save(c)
        self._log(
            f"Delegation {c.commitment_id} funded: ${amount} escrow held for "
            f"'{c.task_title}' until {c.deadline.isoformat()}"
        )
        self._cold(
            "delegation_created",
            {
                "commitment_id": c.commitment_id,
                "task_title": c.task_title,
                "escrow_amount": str(amount),
                "deadline": c.deadline.isoformat(),
                "reason": c.reason,
            },
        )
        self._audit(c, "created", survival_state=survival_state, debt=debt)

    # -- public API ---------------------------------------------------------

    def is_eligible(self, candidate: TaskCandidate) -> bool:
        return candidate.task_type in DELEGATION_ELIGIBLE_TYPES

    def escrow_amount_for(self, candidate: TaskCandidate) -> Decimal:
        """Determine the escrow for a candidate: min-viable payout, capped by
        the fixed delegation budget, floored at the minimum payout."""
        pay = candidate.estimated_pay or Decimal("0.00")
        capped = min(pay, MAX_ESCROW_PER_DELEGATION)
        if capped < MIN_ESCROW_PER_DELEGATION:
            capped = min(MIN_ESCROW_PER_DELEGATION, MAX_ESCROW_PER_DELEGATION)
        return capped.quantize(Decimal("0.01"))

    def try_delegate(
        self,
        candidate: TaskCandidate,
        source_task_id: str,
        reason: str,
        survival_state: str = "",
        debt: Optional[Decimal] = None,
    ) -> DelegationResult:
        """Attempt to delegate a task the agent itself could not execute.

        Funding flows through ``approval_gate`` + ``Wallet.ai_spend`` (§14):
        small escrows execute immediately (escrow held), large ones wait for
        the veto window. No money ever moves OUT of the wallet here — the
        escrow is merely held for a human who has not yet delivered.
        """
        if not self.is_eligible(candidate):
            return DelegationResult(
                status="ineligible",
                reason=f"task_type={candidate.task_type.value} not human-delegable",
            )
        if self.wallet.debt > Decimal("5.00"):
            return DelegationResult(
                status="blocked_debt",
                reason=(
                    f"AI spend blocked: debt ${self.wallet.debt} exceeds $5.00 — "
                    "spending on a human would be reckless"
                ),
            )

        amount = self.escrow_amount_for(candidate)
        reason_str = f"human delegation: {candidate.title} ({reason})"
        decision, pending = self.approval_gate.request_spend(
            amount, DELEGATION_FUNDING_CERTAINTY, reason_str
        )

        if decision is SpendDecision.EXECUTED_IMMEDIATELY:
            try:
                debited = self.wallet.ai_spend(
                    SpendRequest(amount=amount, certainty=DELEGATION_FUNDING_CERTAINTY)
                )
            except WalletError as exc:
                logger.warning("Delegation funding rejected by wallet gates: %s", exc)
                return DelegationResult(status="blocked_wallet", reason=str(exc))
            self.wallet.hold_escrow(debited)
            c = self._new_commitment(candidate, debited, reason)
            self._activate_funded(c, debited, survival_state=survival_state, debt=debt)
            return DelegationResult(
                status="created",
                commitment_id=c.commitment_id,
                escrow_amount=debited,
                reason=reason,
            )

        assert pending is not None  # PENDING always carries a PendingSpend
        c = self._new_commitment(candidate, amount, reason)
        c.pending_spend_id = pending.spend_id
        self._commitments[c.commitment_id] = c
        self._pending_spend_index[pending.spend_id] = c.commitment_id
        self._save(c)
        self._log(
            f"Delegation {c.commitment_id} awaits veto window (deadline "
            f"{pending.veto_deadline.isoformat()}): ${amount} for '{candidate.title}'"
        )
        self._cold(
            "delegation_pending_veto",
            {
                "commitment_id": c.commitment_id,
                "task_title": c.task_title,
                "escrow_amount": str(amount),
                "veto_deadline": pending.veto_deadline.isoformat(),
                "reason": reason,
            },
        )
        self._audit(c, "pending_veto", survival_state=survival_state, debt=debt)
        return DelegationResult(
            status="pending_veto",
            commitment_id=c.commitment_id,
            escrow_amount=amount,
            veto_deadline=pending.veto_deadline.isoformat(),
            reason=reason,
        )

    def finalize_pending_funding(
        self,
        spend_id: str,
        amount: Decimal,
        survival_state: str = "",
        debt: Optional[Decimal] = None,
    ) -> bool:
        """Complete a delegation whose §14 spend just auto-approved.

        Called by the survival loop's ``resolve_pending_spends`` after it has
        executed ``Wallet.ai_spend`` — the money is already debited from the
        free pool, so this tags it as escrow-held and activates the commitment.
        """
        commitment_id = self._pending_spend_index.get(spend_id)
        if commitment_id is None:
            return False
        c = self._commitments.get(commitment_id)
        if c is None or c.escrow_funded:
            return False
        self.wallet.hold_escrow(amount)
        self._activate_funded(c, amount, survival_state=survival_state, debt=debt)
        return True

    def claim_delivery(self, commitment_id: str) -> Commitment:
        """A human claims the task is delivered — the commitment moves to
        DELIVERED and waits for verification. Money has not moved."""
        c = self._require_active(commitment_id)
        c.status = CommitmentStatus.DELIVERED
        self._save(c)
        self._log(f"Delegation {commitment_id}: human claims delivery — awaiting verification")
        self._audit(c, "delivered")
        return c

    def release_to_human(
        self,
        commitment_id: str,
        verification: str,
        survival_state: str = "",
        debt: Optional[Decimal] = None,
    ) -> Decimal:
        """Release escrow to the human — ONLY on verified delivery.

        §20 rule 1 is absolute: any release without verification evidence is a
        prepayment and is refused by the unscoped hard rule. The escrow amount
        leaves the wallet here; ``Wallet.escrow_refund`` never does.
        """
        c = self._commitments.get(commitment_id)
        if c is None:
            raise DelegationError(f"No commitment {commitment_id}")
        if c.status in (CommitmentStatus.RELEASED, CommitmentStatus.EXPIRED, CommitmentStatus.CANCELLED):
            raise DelegationError(f"Commitment {commitment_id} already {c.status.value}")
        if not c.escrow_funded:
            raise DelegationError(f"Commitment {commitment_id} escrow not funded")

        # The no-upfront-payment invariant: releasing without verification IS
        # an upfront payment to a human. The hard rule has no bypass.
        if not verification.strip():
            enforce_no_upfront_payment(
                f"delegation {commitment_id} release without verified delivery"
            )

        amount = c.escrow_amount
        self.wallet.escrow_release(amount)
        c.status = CommitmentStatus.RELEASED
        c.verification = verification.strip()
        c.released_at = datetime.now(timezone.utc)
        self._save(c)
        self._log(
            f"Delegation {commitment_id}: escrow ${amount} released to human on "
            f"verified delivery ({c.verification[:60]})"
        )
        self._cold(
            "escrow_released",
            {
                "commitment_id": commitment_id,
                "task_title": c.task_title,
                "escrow_amount": str(amount),
                "verification": c.verification,
            },
        )
        self._audit(c, "released", survival_state=survival_state, debt=debt)
        return amount

    def refund(self, commitment_id: str, status: CommitmentStatus) -> Decimal:
        """Refund escrow to the free pool (deadline expiry / cancellation)."""
        c = self._commitments.get(commitment_id)
        if c is None:
            raise DelegationError(f"No commitment {commitment_id}")
        if not c.escrow_funded:
            raise DelegationError(f"Commitment {commitment_id} escrow not funded")
        amount = c.escrow_amount
        self.wallet.escrow_refund(amount)
        c.status = status
        self._save(c)
        self._log(
            f"Delegation {commitment_id}: escrow ${amount} refunded (status={status.value})"
        )
        self._cold(
            "escrow_refunded",
            {
                "commitment_id": commitment_id,
                "task_title": c.task_title,
                "escrow_amount": str(amount),
                "status": status.value,
            },
        )
        self._audit(c, "refunded")
        return amount

    def expire_delegations(
        self,
        now: Optional[datetime] = None,
        survival_state: str = "",
        debt: Optional[Decimal] = None,
    ) -> list[dict[str, Any]]:
        """Refund every commitment whose deadline passed without verified
        delivery. Returns a list of refund records. Idempotent and safe to run
        on every survival tick."""
        refunds: list[dict[str, Any]] = []
        for commitment_id in list(self._commitments):
            c = self._commitments[commitment_id]
            if c.is_active() and c.is_expired(now):
                amount = self.refund(commitment_id, CommitmentStatus.EXPIRED)
                refunds.append(
                    {
                        "commitment_id": commitment_id,
                        "task_title": c.task_title,
                        "escrow_refunded": str(amount),
                    }
                )
        return refunds

    def get(self, commitment_id: str) -> Optional[Commitment]:
        return self._commitments.get(commitment_id)

    def list_commitments(self) -> list[Commitment]:
        return list(self._commitments.values())

    def active_escrow_held(self) -> Decimal:
        return sum((c.escrow_amount for c in self._commitments.values() if c.is_active()), Decimal("0.00"))

    def to_dicts(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self._commitments.values()]

    def _require_active(self, commitment_id: str) -> Commitment:
        c = self._commitments.get(commitment_id)
        if c is None:
            raise DelegationError(f"No commitment {commitment_id}")
        if c.status not in (CommitmentStatus.PENDING, CommitmentStatus.DELIVERED):
            raise DelegationError(f"Commitment {commitment_id} not active ({c.status.value})")
        return c