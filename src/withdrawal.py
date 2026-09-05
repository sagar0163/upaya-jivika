"""Withdrawal mechanism — user moves wallet pools to a real bank account.

Deterministic, framework-free (mirrors ``payoneer_webhook.py``). The AI can
never trigger a withdrawal — only the user, via the dashboard UI — so this
module has no survival-state gating of its own; ``Wallet.user_withdraw_free``
/ ``user_withdraw_locked`` already enforce that only the user-facing methods
can touch either pool.

``PayoneerPayoutClient`` is a soft integration point: real payout requires
Payoneer program credentials this codebase doesn't have configured, so it
degrades to "queued for manual processing" (logged + returned in the result)
when unconfigured — the same graceful-fallback pattern ``cold_archive.py``
uses for a missing ``HF_TOKEN``. When configured, it posts the payout request
to Payoneer's API; the response is trusted at face value (Payoneer's webhook,
already wired in ``payoneer_webhook.py``, is the source of truth for whether
money actually moved).
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

PAYOUT_API_URL = "https://api.payoneer.com/v4/programs/payouts"


class WithdrawalPool(str, Enum):
    FREE = "free"
    LOCKED = "locked"


class WithdrawalError(Exception):
    """Raised on an invalid withdrawal request."""


class PayoutStatus(str, Enum):
    SENT = "sent"
    QUEUED_MANUAL = "queued_manual"
    FAILED = "failed"


@dataclass
class WithdrawalResult:
    withdrawal_id: str
    pool: WithdrawalPool
    amount: Decimal
    payout_status: PayoutStatus
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "withdrawal_id": self.withdrawal_id,
            "pool": self.pool.value,
            "amount": str(self.amount),
            "payout_status": self.payout_status.value,
            "detail": self.detail,
        }


class PayoutError(Exception):
    """Raised when a configured payout call fails outright."""


class PayoneerPayoutClient:
    """Thin wrapper around Payoneer's payout API.

    Soft dependency on configuration, not on a package: with no
    ``PAYONEER_API_KEY`` / ``PAYONEER_PROGRAM_ID`` set, ``send_payout``
    returns :attr:`PayoutStatus.QUEUED_MANUAL` instead of raising, so a
    withdrawal request is still recorded and the user can wire up the real
    payout manually until credentials are configured.
    """

    def __init__(self, http_client: Any = None) -> None:
        self._api_key = os.environ.get("PAYONEER_API_KEY")
        self._program_id = os.environ.get("PAYONEER_PROGRAM_ID")
        self._http_client = http_client

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._program_id)

    def send_payout(self, withdrawal_id: str, amount: Decimal, currency: str = "USD") -> tuple[PayoutStatus, str]:
        if not self.is_configured:
            logger.warning(
                "Payoneer payout not configured (PAYONEER_API_KEY/PAYONEER_PROGRAM_ID unset) — "
                "withdrawal %s for $%s queued for manual processing",
                withdrawal_id,
                amount,
            )
            return PayoutStatus.QUEUED_MANUAL, "Payoneer credentials not configured"

        if self._http_client is None:
            import httpx

            self._http_client = httpx.Client(timeout=30.0)

        try:
            resp = self._http_client.post(
                f"{PAYOUT_API_URL}/{self._program_id}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "client_reference_id": withdrawal_id,
                    "amount": str(amount),
                    "currency": currency,
                    "description": "upaya-jivika wallet withdrawal",
                },
            )
            resp.raise_for_status()
            return PayoutStatus.SENT, "Payout request accepted by Payoneer"
        except Exception as exc:
            logger.error("Payoneer payout request failed for %s: %s", withdrawal_id, exc)
            return PayoutStatus.FAILED, str(exc)


def process_withdrawal(
    wallet: Any,
    pool: WithdrawalPool,
    amount: Decimal,
    payout_client: Optional[PayoneerPayoutClient] = None,
) -> WithdrawalResult:
    """Debit ``pool`` on ``wallet`` and attempt to send the real-world payout.

    Debits the wallet *before* attempting payout: a queued-manual or failed
    payout still represents money the user asked to move out of the AI's
    reach, so the pool must reflect that immediately rather than waiting on
    a third-party API call that may never resolve.
    """
    if amount <= 0:
        raise WithdrawalError("Withdrawal amount must be positive")

    if pool is WithdrawalPool.FREE:
        wallet.user_withdraw_free(amount)
    elif pool is WithdrawalPool.LOCKED:
        wallet.user_withdraw_locked(amount)
    else:  # pragma: no cover - exhaustive enum
        raise WithdrawalError(f"Unknown pool: {pool}")

    withdrawal_id = str(uuid.uuid4())
    client = payout_client or PayoneerPayoutClient()
    status, detail = client.send_payout(withdrawal_id, amount)

    return WithdrawalResult(
        withdrawal_id=withdrawal_id,
        pool=pool,
        amount=amount,
        payout_status=status,
        detail=detail,
    )
