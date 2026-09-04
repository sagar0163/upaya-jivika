"""Dual-pool wallet: locked (user-only, debt payments) + free (surplus, AI-investable).

Rules from artifact.md §4:
- Locked pool: filled only by debt payments, user can withdraw, AI can NEVER access.
- Free pool: surplus after debt cleared, user can withdraw, AI can invest max 30%
  per action with >95% ROI certainty gate.
- AI spend blocked entirely when debt > $5.00.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class WalletError(Exception):
    """Raised on illegal wallet operations."""


class SpendRequest(BaseModel):
    """A request by the AI to spend from the free pool."""

    amount: Decimal = Field(gt=0, description="Amount the AI wants to spend.")
    certainty: Decimal = Field(
        ge=0, le=1, description="ROI confidence 0-1."
    )


class Wallet(BaseModel):
    """Immutable-structure dual-pool wallet.

    The locked pool is *logically* immutable from the AI side — no public
    method exposes a way to debit it.  The user can withdraw via
    ``user_withdraw_locked``.
    """

    locked: Decimal = Field(default=Decimal("0.00"), ge=0)
    free: Decimal = Field(default=Decimal("0.00"), ge=0)
    debt: Decimal = Field(default=Decimal("0.00"), ge=0)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _dec(v: float | int | str | Decimal) -> Decimal:
        return Decimal(str(v))

    # -- earnings ----------------------------------------------------------

    def credit_earned(self, amount: float | Decimal) -> dict[str, Decimal]:
        """Credit earnings: debt is repaid first, surplus goes to free pool.

        Returns a breakdown of where the money went.
        """
        amt = self._dec(amount)
        if amt <= 0:
            raise WalletError("Credit amount must be positive")

        debt_repaid = Decimal("0")
        to_free = Decimal("0")

        if self.debt > 0:
            debt_repaid = min(amt, self.debt)
            self.debt -= debt_repaid
            self.locked += debt_repaid  # debt payments go to locked pool
            to_free = amt - debt_repaid
        else:
            to_free = amt

        self.free += to_free
        return {"debt_repaid": debt_repaid, "to_free": to_free, "to_locked": debt_repaid}

    # -- user withdrawals (user can touch both pools) ----------------------

    def user_withdraw_free(self, amount: float | Decimal) -> Decimal:
        amt = self._dec(amount)
        if amt <= 0:
            raise WalletError("Withdrawal amount must be positive")
        if amt > self.free:
            raise WalletError("Insufficient free pool balance")
        self.free -= amt
        return amt

    def user_withdraw_locked(self, amount: float | Decimal) -> Decimal:
        amt = self._dec(amount)
        if amt <= 0:
            raise WalletError("Withdrawal amount must be positive")
        if amt > self.locked:
            raise WalletError("Insufficient locked pool balance")
        self.locked -= amt
        return amt

    # -- AI spend (restricted) ---------------------------------------------

    def ai_spend(
        self,
        request: SpendRequest,
        spend_blocked_threshold: Decimal = Decimal("5.00"),
        max_fraction: Decimal = Decimal("0.30"),
        min_certainty: Decimal = Decimal("0.95"),
    ) -> Decimal:
        """AI requests to spend from the free pool.

        Enforces:
        1. Debt must be <= ``spend_blocked_threshold`` (default $5).
        2. Amount must not exceed ``max_fraction`` (30 %) of free pool.
        3. Certainty must be >= ``min_certainty`` (95 %).

        Returns the amount debited.
        """
        if self.debt > spend_blocked_threshold:
            raise WalletError(
                f"AI spend blocked: debt ${self.debt} exceeds ${spend_blocked_threshold}"
            )

        if request.certainty < min_certainty:
            raise WalletError(
                f"AI spend blocked: certainty {request.certainty} < {min_certainty}"
            )

        max_allowed = (self.free * max_fraction).quantize(Decimal("0.01"))
        if request.amount > max_allowed:
            raise WalletError(
                f"AI spend blocked: {request.amount} exceeds {max_fraction*100}% "
                f"of free pool ({max_allowed})"
            )

        if request.amount > self.free:
            raise WalletError("AI spend blocked: insufficient free pool balance")

        self.free -= request.amount
        return request.amount

    # -- properties --------------------------------------------------------

    @property
    def total_balance(self) -> Decimal:
        return self.locked + self.free

    @property
    def net_worth(self) -> Decimal:
        return self.total_balance - self.debt
