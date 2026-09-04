"""Tests for src/payoneer_webhook.py — signature verification and parsing."""

import hashlib
import hmac
from decimal import Decimal

import pytest

from src.payoneer_webhook import (
    PaymentStatus,
    PayoneerWebhookError,
    parse_webhook_payload,
    verify_signature,
)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_valid_signature_accepted(self):
        secret = "topsecret"
        body = b'{"payment_id": "p1", "amount": "5.00", "status": "completed"}'
        sig = _sign(secret, body)
        assert verify_signature(secret, body, sig) is True

    def test_valid_signature_with_sha256_prefix_accepted(self):
        secret = "topsecret"
        body = b'{"payment_id": "p1"}'
        sig = "sha256=" + _sign(secret, body)
        assert verify_signature(secret, body, sig) is True

    def test_wrong_secret_rejected(self):
        body = b'{"payment_id": "p1"}'
        sig = _sign("correct", body)
        assert verify_signature("wrong", body, sig) is False

    def test_tampered_body_rejected(self):
        secret = "topsecret"
        sig = _sign(secret, b'{"amount": "5.00"}')
        assert verify_signature(secret, b'{"amount": "500.00"}', sig) is False

    def test_missing_secret_rejected(self):
        body = b"{}"
        sig = _sign("whatever", body)
        assert verify_signature("", body, sig) is False

    def test_missing_signature_rejected(self):
        assert verify_signature("secret", b"{}", "") is False


class TestParseWebhookPayload:
    def test_parses_standard_payload(self):
        event = parse_webhook_payload(
            {"payment_id": "pay_123", "amount": "12.50", "currency": "USD", "status": "completed"}
        )
        assert event.payment_id == "pay_123"
        assert event.amount == Decimal("12.50")
        assert event.currency == "USD"
        assert event.status is PaymentStatus.COMPLETED
        assert event.is_completed is True

    def test_accepts_field_name_variants(self):
        event = parse_webhook_payload(
            {"id": "txn_9", "payment_amount": "3", "payment_status": "paid"}
        )
        assert event.payment_id == "txn_9"
        assert event.amount == Decimal("3")
        assert event.status is PaymentStatus.COMPLETED
        assert event.currency == "USD"  # default

    def test_pending_status_not_completed(self):
        event = parse_webhook_payload({"payment_id": "p1", "amount": "1", "status": "processing"})
        assert event.status is PaymentStatus.PENDING
        assert event.is_completed is False

    def test_failed_status(self):
        event = parse_webhook_payload({"payment_id": "p1", "amount": "1", "status": "reversed"})
        assert event.status is PaymentStatus.FAILED

    def test_unrecognised_status_maps_to_unknown(self):
        event = parse_webhook_payload({"payment_id": "p1", "amount": "1", "status": "some_new_state"})
        assert event.status is PaymentStatus.UNKNOWN
        assert event.is_completed is False

    def test_missing_payment_id_raises(self):
        with pytest.raises(PayoneerWebhookError):
            parse_webhook_payload({"amount": "1", "status": "completed"})

    def test_missing_amount_raises(self):
        with pytest.raises(PayoneerWebhookError):
            parse_webhook_payload({"payment_id": "p1", "status": "completed"})

    def test_zero_amount_raises(self):
        with pytest.raises(PayoneerWebhookError):
            parse_webhook_payload({"payment_id": "p1", "amount": "0", "status": "completed"})

    def test_negative_amount_raises(self):
        with pytest.raises(PayoneerWebhookError):
            parse_webhook_payload({"payment_id": "p1", "amount": "-5", "status": "completed"})

    def test_invalid_amount_raises(self):
        with pytest.raises(PayoneerWebhookError):
            parse_webhook_payload({"payment_id": "p1", "amount": "not-a-number", "status": "completed"})

    def test_raw_payload_preserved(self):
        payload = {"payment_id": "p1", "amount": "1", "status": "completed", "extra": "field"}
        event = parse_webhook_payload(payload)
        assert event.raw == payload
