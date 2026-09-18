"""Tests for src/payoneer_webhook.py — signature verification and parsing."""

import hashlib
import hmac
import json
import time
import uuid
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


def _fresh_body(**fields) -> bytes:
    """A webhook body with a fresh timestamp+nonce plus any extra fields."""
    payload = {"timestamp": str(time.time()), "nonce": str(uuid.uuid4())}
    payload.update(fields)
    return json.dumps(payload).encode()


class TestVerifySignature:
    def test_valid_signature_accepted(self):
        secret = "topsecret"
        body = _fresh_body(payment_id="p1", amount="5.00", status="completed")
        sig = _sign(secret, body)
        assert verify_signature(secret, body, sig) is True

    def test_valid_signature_with_sha256_prefix_accepted(self):
        secret = "topsecret"
        body = _fresh_body(payment_id="p1")
        sig = "sha256=" + _sign(secret, body)
        assert verify_signature(secret, body, sig) is True

    def test_wrong_secret_rejected(self):
        body = _fresh_body(payment_id="p1")
        sig = _sign("correct", body)
        assert verify_signature("wrong", body, sig) is False

    def test_tampered_body_rejected(self):
        secret = "topsecret"
        valid = _fresh_body(amount="5.00")
        sig = _sign(secret, valid)
        tampered = json.dumps(
            {"timestamp": str(time.time()), "nonce": str(uuid.uuid4()), "amount": "500.00"}
        ).encode()
        assert verify_signature(secret, tampered, sig) is False

    def test_missing_secret_rejected(self):
        body = _fresh_body()
        sig = _sign("whatever", body)
        assert verify_signature("", body, sig) is False

    def test_missing_signature_rejected(self):
        assert verify_signature("secret", _fresh_body(), "") is False

    def test_stale_valid_signature_rejected(self):
        """A valid HMAC older than the freshness window is a replay."""
        secret = "topsecret"
        stale = time.time() - 600
        body = json.dumps(
            {"timestamp": str(stale), "nonce": str(uuid.uuid4()), "payment_id": "p1"}
        ).encode()
        sig = _sign(secret, body)
        assert verify_signature(secret, body, sig) is False

    def test_future_valid_signature_rejected(self):
        secret = "topsecret"
        future = time.time() + 600
        body = json.dumps(
            {"timestamp": str(future), "nonce": str(uuid.uuid4()), "payment_id": "p1"}
        ).encode()
        sig = _sign(secret, body)
        assert verify_signature(secret, body, sig) is False

    def test_nonce_reuse_rejected(self):
        secret = "topsecret"
        body = _fresh_body(payment_id="p1")
        sig = _sign(secret, body)
        assert verify_signature(secret, body, sig) is True
        assert verify_signature(secret, body, sig) is False

    def test_missing_timestamp_or_nonce_rejected(self):
        secret = "topsecret"
        without_ts = json.dumps({"nonce": str(uuid.uuid4()), "payment_id": "p1"}).encode()
        without_nonce = json.dumps({"timestamp": str(time.time()), "payment_id": "p1"}).encode()
        assert verify_signature(secret, without_ts, _sign(secret, without_ts)) is False
        assert verify_signature(secret, without_nonce, _sign(secret, without_nonce)) is False

    def test_invalid_timestamp_rejected(self):
        secret = "topsecret"
        body = json.dumps(
            {"timestamp": "not-a-number", "nonce": str(uuid.uuid4()), "payment_id": "p1"}
        ).encode()
        sig = _sign(secret, body)
        assert verify_signature(secret, body, sig) is False


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