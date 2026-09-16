"""Revenue split policy and auto-payout scheduler (issue #75).

Mirrors the forage/Franklin pattern: milestone-based revenue splits
(owner/reinvest/reserve) that activate as balance crosses thresholds,
with automatic owner payout scheduling.

The split is applied to *free-pool earnings* only — debt repayment
and locked-pool movements are untouched (see wallet.py §4).

Design decisions:
- Thresholds are balance-based (not day-count) — simpler, deterministic,
  and matches the forage/Franklin precedent.
- Fractions are stored as Decimals 0-1 that must sum to 1.0.
- Owner share accumulates in a separate "owner_owed" bucket; auto-payout
  drains it when above the minimum cadence, reusing process_withdrawal.
- Reserve share goes to the locked pool (the "floor"), reinvest stays in
  free (subject to existing ai_spend/approval_gate gates).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Split fractions per threshold tier
# ---------------------------------------------------------------------------

class SplitFractions(BaseModel):
    """Owner / reinvest / reserve fractions for one tier.

    Must sum to 1.0 (±0.001 for rounding).
    """

    owner: Decimal = Field(ge=0, le=1, description="Fraction paid out to owner")
    reinvest: Decimal = Field(ge=0, le=1, description="Fraction kept in free pool for AI spend")
    reserve: Decimal = Field(ge=0, le=1, description="Fraction moved to locked pool (floor)")

    @field_validator("owner", "reinvest", "reserve", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))

    @model_validator(mode="after")
    def _validate_sum(self) -> "SplitFractions":
        total = self.owner + self.reinvest + self.reserve
        if abs(total - Decimal("1")) > Decimal("0.001"):
            raise ValueError(
                f"owner + reinvest + reserve must sum to 1.0, got {total}"
            )
        return self


class ThresholdTier(BaseModel):
    """A single threshold tier: when free pool >= ``min_balance``, apply these fractions."""

    min_balance: Decimal = Field(
        ge=0,
        description="Minimum free-pool balance to activate this tier",
    )
    fractions: SplitFractions

    @field_validator("min_balance", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))


# ---------------------------------------------------------------------------
# Revenue Split Policy (persisted config)
# ---------------------------------------------------------------------------

class RevenueSplitPolicy(BaseModel):
    """Milestone-based revenue split policy — persisted in app_settings.

    Tiers are evaluated in order; the first tier whose ``min_balance``
    is <= the current free-pool balance wins.  If no tier matches, the
    most conservative (all-reinvest) fallback applies.

    Example (forage/Franklin-inspired):
        tier 0: min_balance=0     → 0/100/0  (owner/reinvest/reserve)
        tier 1: min_balance=50    → 10/80/10
        tier 2: min_balance=200   → 30/50/20
    """

    tiers: list[ThresholdTier] = Field(
        default_factory=lambda: [
            ThresholdTier(
                min_balance=Decimal("0"),
                fractions=SplitFractions(
                    owner=Decimal("0"),
                    reinvest=Decimal("1"),
                    reserve=Decimal("0"),
                ),
            ),
        ],
        description="Ordered threshold tiers (first match wins)",
    )
    auto_payout_enabled: bool = Field(
        default=False,
        description="Whether owner share auto-payout is active",
    )
    auto_payout_minimum: Decimal = Field(
        default=Decimal("10.00"),
        ge=0,
        description="Minimum owner_owed balance to trigger auto-payout",
    )
    auto_payout_cadence_hours: int = Field(
        default=24,
        ge=1,
        description="Hours between auto-payout attempts",
    )
    seed_capital_repaid: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        description="Running total of owner payouts (seed-capital repayment tracker)",
    )

    @field_validator("tiers")
    @classmethod
    def _sorted_tiers(cls, v: list[ThresholdTier]) -> list[ThresholdTier]:
        return sorted(v, key=lambda t: t.min_balance)

    def resolve(self, free_balance: Decimal) -> SplitFractions:
        """Return the matching split fractions for the given free-pool balance.

        Tiers are sorted ascending; the *last* tier whose min_balance <=
        free_balance wins (highest applicable threshold).
        """
        if not self.tiers:
            return SplitFractions(
                owner=Decimal("0"),
                reinvest=Decimal("1"),
                reserve=Decimal("0"),
            )

        best = self.tiers[0].fractions
        for tier in self.tiers:
            if free_balance >= tier.min_balance:
                best = tier.fractions
            else:
                break
        return best


# ---------------------------------------------------------------------------
# Owner payout record (audit trail)
# ---------------------------------------------------------------------------

class PayoutRecord(BaseModel):
    """A single owner payout — idempotent by payout_id."""

    payout_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    amount: Decimal
    initiated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed: bool = False
    withdrawal_id: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "payout_id": self.payout_id,
            "amount": str(self.amount),
            "initiated_at": self.initiated_at.isoformat(),
            "completed": self.completed,
            "withdrawal_id": self.withdrawal_id,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Revenue Split Engine (stateless — operates on wallet + policy)
# ---------------------------------------------------------------------------

class RevenueSplitEngine:
    """Applies the revenue split and manages owner payout accumulation.

    State is stored on the wallet (owner_owed field) and in the
    persistence layer (policy + payout history).  This engine is
    stateless — it reads/writes through its arguments.
    """

    def __init__(self, policy: RevenueSplitPolicy | None = None) -> None:
        self.policy = policy or RevenueSplitPolicy()

    def apply_split(
        self,
        to_free: Decimal,
        wallet: Any,
    ) -> dict[str, Decimal]:
        """Split ``to_free`` (new free-pool earnings after debt repayment) into
        owner/reinvest/reserve according to the current policy tier.

        Mutates the wallet in place:
        - ``reserve`` fraction → wallet.locked (floor)
        - ``reinvest`` fraction → stays in wallet.free (already there, no-op)
        - ``owner`` fraction → wallet.owner_owed (new field)

        Returns the breakdown of where each fraction went.
        """
        if to_free <= 0:
            return {"owner": Decimal("0"), "reinvest": Decimal("0"), "reserve": Decimal("0")}

        # Tier is evaluated against the post-credit free balance — the earnings
        # just landed, so forage/Franklin-style milestone checks use the new
        # balance to pick the split that applies to this payment.
        fractions = self.policy.resolve(wallet.free)

        total = to_free
        owner_share = (total * fractions.owner).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        reserve_share = (total * fractions.reserve).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        # Remainder to reinvest (avoids rounding drift)
        reinvest_share = total - owner_share - reserve_share

        # Owner share moves to owner_owed (deducted from free)
        wallet.free -= owner_share
        if not hasattr(wallet, "owner_owed") or wallet.owner_owed is None:
            wallet.owner_owed = Decimal("0")
        wallet.owner_owed += owner_share

        # Reserve share moves to locked pool
        wallet.free -= reserve_share
        wallet.locked += reserve_share

        # Reinvest stays in free (already there, no-op)

        logger.info(
            "Revenue split applied: owner=$%s reinvest=$%s reserve=$%s (tier: %s)",
            owner_share,
            reinvest_share,
            reserve_share,
            fractions.model_dump(),
        )

        return {
            "owner": owner_share,
            "reinvest": reinvest_share,
            "reserve": reserve_share,
        }

    def check_auto_payout(
        self,
        wallet: Any,
        payout_history: list[PayoutRecord],
        payout_client: Any = None,
        now: datetime | None = None,
    ) -> PayoutRecord | None:
        """If auto-payout is enabled, owner_owed >= minimum, and cadence has
        elapsed since last payout, initiate a payout.

        Returns the PayoutRecord if a payout was made, None otherwise.
        """
        if not self.policy.auto_payout_enabled:
            return None

        owner_owed = getattr(wallet, "owner_owed", None) or Decimal("0")
        if owner_owed < self.policy.auto_payout_minimum:
            return None

        # Check cadence — last payout must be >= cadence_hours ago
        now = now or datetime.now(timezone.utc)
        if payout_history:
            last_completed = max(
                (p for p in payout_history if p.completed),
                key=lambda p: p.initiated_at,
                default=None,
            )
            if last_completed is not None:
                elapsed = now - last_completed.initiated_at
                if elapsed.total_seconds() < self.policy.auto_payout_cadence_hours * 3600:
                    return None

        # Initiate payout — debits the dedicated ``owner_owed`` bucket (the money was
        # already removed from free during the split), reusing the standard
        # payout client + audit path.
        from src.withdrawal import PayoutStatus, WithdrawalPool, process_withdrawal

        amount = owner_owed
        record = PayoutRecord(amount=amount)

        try:
            result = process_withdrawal(wallet, WithdrawalPool.OWNER_OWED, amount, payout_client)
            if result.payout_status is PayoutStatus.FAILED:
                # Money never moved — restore the earmark so the next cadence
                # retries. The attempt is still recorded for the audit trail.
                wallet.owner_owed += amount
                record.completed = False
                record.error = result.detail or "payout failed"
                logger.error("Owner auto-payout FAILED for %s: %s", record.payout_id, result.detail)
            else:
                # SENT or QUEUED_MANUAL: the owner's share has left the AI's
                # reach and is queued — mark complete + track seed repayment.
                record.completed = True
                record.withdrawal_id = result.withdrawal_id
                self.policy.seed_capital_repaid += amount
                logger.info(
                    "Owner auto-payout: $%s (payout_id=%s, withdrawal=%s)",
                    amount,
                    record.payout_id,
                    result.withdrawal_id,
                )
        except Exception as exc:
            # process_withdrawal debits the pool *before* attempting payout, so
            # an unexpected exception leaves owner_owed drained with nothing sent —
            # restore it so the money stays earmarked for the next attempt.
            wallet.owner_owed += amount
            record.completed = False
            record.error = str(exc)
            logger.error("Owner auto-payout failed: %s", exc)

        return record

    def get_current_tier(self, free_balance: Decimal) -> dict[str, Any]:
        """Return info about which tier is currently active."""
        fractions = self.policy.resolve(free_balance)
        return {
            "tier_fractions": {
                "owner": str(fractions.owner),
                "reinvest": str(fractions.reinvest),
                "reserve": str(fractions.reserve),
            },
            "free_balance": str(free_balance),
        }
