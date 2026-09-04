"""Payoneer payment webhook — confirms real money landed (artifact.md §20).

This is the "payment confirmation" gap: the survival loop must know when a
Payoneer payout actually clears, both to credit the wallet and to close the
payment-window-monitoring loop that scam detection (§20) depends on — a task
whose payment window expires with no matching webhook is a suspected scam.

Payoneer's exact webhook payload shape depends on which Payoneer product the
account is enrolled in (Mass Payout API vs. Checkout vs. Partner Program) and
is only available under a signed integration agreement, so this module is
deliberately defensive: it accepts a handful of common field-name variants
rather than betting on one exact schema, and everything is driven by
``PAYONEER_WEBHOOK_SECRET`` so nothing here is guessable. Once real payload
samples are observed in production, narrow :func:`parse_webhook_payload` to
match exactly.

Design constraints (mirroring ``audit_trail.py`` / ``alert_system.py``):
- Framework-free and deterministic: no LLM calls, no randomness, no I/O.
- Signature verification fails closed: a missing/incorrect secret or
  signature is always rejected, never silently accepted.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: HTTP header Payoneer (or a webhook relay in front of it) is expected to
#: send the HMAC-SHA256 signature of the raw request body in.
SIGNATURE_HEADER = "X-Payoneer-Signature"


class PayoneerWebhookError(Exception):
    """Raised when a webhook payload is malformed or fails verification."""


class PaymentStatus(str, Enum):
    """Normalised payment status, independent of Payoneer's own wording."""

    COMPLETED = "completed"
    PENDING = "pending"
    FAILED = "failed"
    UNKNOWN = "unknown"


#: Maps the raw status strings Payoneer/partners commonly use onto our
#: normalised :class:`PaymentStatus`. Extend as real payloads are observed.
_STATUS_MAP: dict[str, PaymentStatus] = {
    "completed": PaymentStatus.COMPLETED,
    "success": PaymentStatus.COMPLETED,
    "paid": PaymentStatus.COMPLETED,
    "payment_completed": PaymentStatus.COMPLETED,
    "clearing_completed": PaymentStatus.COMPLETED,
    "pending": PaymentStatus.PENDING,
    "processing": PaymentStatus.PENDING,
    "in_progress": PaymentStatus.PENDING,
    "failed": PaymentStatus.FAILED,
    "cancelled": PaymentStatus.FAILED,
    "canceled": PaymentStatus.FAILED,
    "rejected": PaymentStatus.FAILED,
    "reversed": PaymentStatus.FAILED,
}

#: Field-name variants accepted for each logical field, tried in order.
_PAYMENT_ID_KEYS = ("payment_id", "id", "transaction_id", "payout_id")
_AMOUNT_KEYS = ("amount", "payment_amount", "payout_amount", "value")
_CURRENCY_KEYS = ("currency", "payment_currency")
_STATUS_KEYS = ("status", "payment_status", "event", "event_type")


class PayoneerWebhookEvent(BaseModel):
    """A normalised, verified Payoneer payment notification."""

    payment_id: str
    amount: Decimal = Field(gt=0)
    currency: str = "USD"
    status: PaymentStatus
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_completed(self) -> bool:
        return self.status is PaymentStatus.COMPLETED


def verify_signature(secret: str, raw_body: bytes, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature of ``raw_body`` using ``secret``.

    Fails closed: any empty/missing input is treated as invalid. Uses
    :func:`hmac.compare_digest` to avoid timing-attack leakage.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Accept a "sha256=<hex>" prefix (a common convention, e.g. GitHub/Stripe)
    # as well as a bare hex digest.
    candidate = signature.strip()
    if candidate.startswith("sha256="):
        candidate = candidate[len("sha256=") :]
    return hmac.compare_digest(expected, candidate.lower())


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Optional[Any]:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def parse_webhook_payload(payload: dict[str, Any]) -> PayoneerWebhookEvent:
    """Parse a verified webhook JSON body into a :class:`PayoneerWebhookEvent`.

    Raises :class:`PayoneerWebhookError` if required fields are missing or
    malformed. Unrecognised status strings map to :attr:`PaymentStatus.UNKNOWN`
    rather than raising, since Payoneer may add new intermediate states.
    """
    payment_id = _first_present(payload, _PAYMENT_ID_KEYS)
    if not payment_id:
        raise PayoneerWebhookError(
            f"Missing payment id (expected one of {_PAYMENT_ID_KEYS})"
        )

    raw_amount = _first_present(payload, _AMOUNT_KEYS)
    if raw_amount is None:
        raise PayoneerWebhookError(
            f"Missing amount (expected one of {_AMOUNT_KEYS})"
        )
    try:
        amount = Decimal(str(raw_amount))
    except InvalidOperation as exc:
        raise PayoneerWebhookError(f"Invalid amount: {raw_amount!r}") from exc
    if amount <= 0:
        raise PayoneerWebhookError(f"Amount must be positive, got {amount}")

    currency = _first_present(payload, _CURRENCY_KEYS) or "USD"

    raw_status = _first_present(payload, _STATUS_KEYS)
    status = _STATUS_MAP.get(str(raw_status).lower(), PaymentStatus.UNKNOWN) if raw_status else PaymentStatus.UNKNOWN
    if status is PaymentStatus.UNKNOWN:
        logger.warning("Unrecognised Payoneer webhook status: %r", raw_status)

    return PayoneerWebhookEvent(
        payment_id=str(payment_id),
        amount=amount,
        currency=str(currency),
        status=status,
        raw=payload,
    )
