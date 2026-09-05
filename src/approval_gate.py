"""Human approval gate for AI free-pool spending (artifact.md §14 critical gap).

Design: a **veto window**, not pre-approval. The survival loop runs
autonomously while the user is away (debt ticks and cron jobs run with
nobody watching), so blocking every spend on a human click would mean a
real decision — or an urgent one — sits frozen indefinitely. Instead:

- Spends below ``VETO_THRESHOLD`` still execute immediately through
  ``Wallet.ai_spend``'s existing automatic gates (debt/certainty/fraction) —
  unchanged from before this module existed.
- Spends at or above the threshold are announced (alert + persisted pending
  record) and held for ``VETO_WINDOW``. If the user rejects it within the
  window, it never executes. If nobody responds, it auto-approves and
  executes when the window elapses — the AI never stalls waiting on a human.

This module owns the deterministic decision (threshold check, window math,
pending-state transitions); ``Wallet.ai_spend`` is still the sole enforcer of
the debt/certainty/fraction gates, and is only called once a spend is
actually approved (immediately, or after the window elapses) — so those
gates are re-checked against wallet state at execution time, not decision
time.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Spends at or above this amount require a veto window instead of executing
#: immediately.
VETO_THRESHOLD = Decimal("2.00")

#: How long a pending spend waits for a user veto before auto-approving.
VETO_WINDOW = timedelta(hours=6)


class SpendDecision(str, Enum):
    EXECUTED_IMMEDIATELY = "executed_immediately"
    PENDING = "pending"
    AUTO_APPROVED = "auto_approved"
    REJECTED = "rejected"


@dataclass
class PendingSpend:
    spend_id: str
    amount: Decimal
    certainty: Decimal
    reason: str
    created_at: datetime
    veto_deadline: datetime
    rejected: bool = False

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.veto_deadline

    def to_dict(self) -> dict[str, Any]:
        return {
            "spend_id": self.spend_id,
            "amount": str(self.amount),
            "certainty": str(self.certainty),
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "veto_deadline": self.veto_deadline.isoformat(),
            "rejected": self.rejected,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PendingSpend":
        return cls(
            spend_id=d["spend_id"],
            amount=Decimal(d["amount"]),
            certainty=Decimal(d["certainty"]),
            reason=d.get("reason", ""),
            created_at=datetime.fromisoformat(d["created_at"]),
            veto_deadline=datetime.fromisoformat(d["veto_deadline"]),
            rejected=d.get("rejected", False),
        )


def requires_veto_window(amount: Decimal, threshold: Decimal = VETO_THRESHOLD) -> bool:
    return amount >= threshold


class ApprovalGate:
    """Tracks pending spend requests and resolves them against the veto window.

    ``store`` is a :class:`~src.persistence.PersistenceStore` — pending
    spends are persisted so a request survives a restart during its window
    (Render free-tier sleep, a redeploy) rather than silently vanishing.
    """

    def __init__(self, store: Any, window: timedelta = VETO_WINDOW, threshold: Decimal = VETO_THRESHOLD) -> None:
        self._store = store
        self.window = window
        self.threshold = threshold

    def request_spend(
        self, amount: Decimal, certainty: Decimal, reason: str
    ) -> tuple[SpendDecision, Optional[PendingSpend]]:
        """Decide whether ``amount`` executes immediately or needs a veto window.

        Returns ``(EXECUTED_IMMEDIATELY, None)`` for a small spend — the
        caller is responsible for actually calling ``Wallet.ai_spend`` in
        that case. Returns ``(PENDING, pending_spend)`` for a large one,
        already persisted; the caller is responsible for notifying the user.
        """
        if not requires_veto_window(amount, self.threshold):
            return SpendDecision.EXECUTED_IMMEDIATELY, None

        now = datetime.now(timezone.utc)
        pending = PendingSpend(
            spend_id=str(uuid.uuid4()),
            amount=amount,
            certainty=certainty,
            reason=reason,
            created_at=now,
            veto_deadline=now + self.window,
        )
        self._store.save_pending_spend(pending.spend_id, pending.to_dict())
        logger.info(
            "Spend request $%s held for veto window until %s: %s",
            amount,
            pending.veto_deadline.isoformat(),
            reason,
        )
        return SpendDecision.PENDING, pending

    def reject(self, spend_id: str) -> bool:
        """User vetoes a pending spend. Returns False if it wasn't found/pending."""
        data = self._store.load_pending_spend(spend_id)
        if data is None:
            return False
        pending = PendingSpend.from_dict(data)
        if pending.is_expired():
            return False
        pending.rejected = True
        self._store.save_pending_spend(spend_id, pending.to_dict())
        logger.info("Spend request %s rejected by user", spend_id)
        return True

    def list_pending(self) -> list[PendingSpend]:
        return [PendingSpend.from_dict(d) for d in self._store.load_pending_spends()]

    def resolve_due(self, now: Optional[datetime] = None) -> list[tuple[PendingSpend, SpendDecision]]:
        """Resolve every pending spend whose veto window has elapsed.

        Returns ``(pending, REJECTED | AUTO_APPROVED)`` for each one resolved
        and removes it from the store. The caller executes the actual
        ``Wallet.ai_spend`` call for each ``AUTO_APPROVED`` entry — this
        method only makes the decision.
        """
        now = now or datetime.now(timezone.utc)
        resolved: list[tuple[PendingSpend, SpendDecision]] = []
        for pending in self.list_pending():
            if pending.rejected:
                self._store.delete_pending_spend(pending.spend_id)
                resolved.append((pending, SpendDecision.REJECTED))
            elif pending.is_expired(now):
                self._store.delete_pending_spend(pending.spend_id)
                resolved.append((pending, SpendDecision.AUTO_APPROVED))
        return resolved
