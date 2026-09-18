"""Integration tests for the /api/webhooks/payoneer endpoint and
SurvivalLoop.record_payment (§20 payment confirmation).
"""

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

from src.payoneer_webhook import parse_webhook_payload
from src.persistence import InMemoryStore

SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _event_payload(**fields) -> dict:
    payload = {"timestamp": str(time.time()), "nonce": str(uuid.uuid4())}
    payload.update(fields)
    return payload


def _webhook_body(**fields) -> bytes:
    return json.dumps(_event_payload(**fields)).encode()


def _client_with_loop(store=None):
    """Build a TestClient wired to a fresh SurvivalLoop, mirroring the
    pattern in tests/test_main_loop.py::TestHealthEndpoint.
    """
    from fastapi.testclient import TestClient

    import main as main_mod

    @main_mod.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
    test_app.router.routes.extend(main_mod.app.router.routes)

    store = store or InMemoryStore()
    loop = main_mod.SurvivalLoop(persistence=store)
    main_mod._loop = loop

    return TestClient(test_app), loop, store


# ---------------------------------------------------------------------------
# SurvivalLoop.record_payment (unit level)
# ---------------------------------------------------------------------------

class TestRecordPayment:
    def test_completed_payment_credits_wallet(self):
        import main as main_mod

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        event = parse_webhook_payload(_event_payload(payment_id="pay_1", amount="3.00", status="completed"))

        result = loop.record_payment(event)

        assert result["processed"] is True
        assert loop.wallet.total_balance == Decimal("3.00")
        assert store.is_payment_processed("pay_1") is True

    def test_completed_payment_repays_debt_first(self):
        import main as main_mod

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        loop.wallet.debt = Decimal("2.00")
        event = parse_webhook_payload(_event_payload(payment_id="pay_2", amount="5.00", status="completed"))

        result = loop.record_payment(event)

        assert result["debt_repaid"] == "2.00"
        assert result["to_free"] == "3.00"
        assert loop.wallet.locked == Decimal("2.00")
        assert loop.wallet.free == Decimal("3.00")

    def test_duplicate_payment_id_not_credited_twice(self):
        import main as main_mod

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        event = parse_webhook_payload(_event_payload(payment_id="pay_3", amount="4.00", status="completed"))

        first = loop.record_payment(event)
        second = loop.record_payment(event)

        assert first["processed"] is True
        assert second["processed"] is False
        assert second["reason"] == "duplicate"
        assert loop.wallet.total_balance == Decimal("4.00")  # not doubled

    def test_pending_status_does_not_credit(self):
        import main as main_mod

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        event = parse_webhook_payload(_event_payload(payment_id="pay_4", amount="4.00", status="pending"))

        result = loop.record_payment(event)

        assert result["processed"] is False
        assert loop.wallet.total_balance == Decimal("0")
        assert store.is_payment_processed("pay_4") is False

    def test_persists_state_after_credit(self):
        import main as main_mod

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        event = parse_webhook_payload(_event_payload(payment_id="pay_5", amount="1.50", status="completed"))

        loop.record_payment(event)

        saved_wallet = store.load_wallet()
        assert Decimal(saved_wallet["free"]) == Decimal("1.50")


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

class TestPayoneerWebhookEndpoint:
    def setup_method(self):
        import os

        self._old_secret = os.environ.get("PAYONEER_WEBHOOK_SECRET")
        os.environ["PAYONEER_WEBHOOK_SECRET"] = SECRET

    def teardown_method(self):
        import os

        if self._old_secret is None:
            os.environ.pop("PAYONEER_WEBHOOK_SECRET", None)
        else:
            os.environ["PAYONEER_WEBHOOK_SECRET"] = self._old_secret

    def test_valid_signature_credits_wallet(self):
        client, loop, store = _client_with_loop()
        body = _webhook_body(payment_id="http_1", amount="2.00", status="completed")
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        assert resp.json()["processed"] is True
        assert loop.wallet.total_balance == Decimal("2.00")

    def test_invalid_signature_rejected(self):
        client, loop, store = _client_with_loop()
        body = _webhook_body(payment_id="http_2", amount="2.00", status="completed")

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": "deadbeef", "Content-Type": "application/json"},
        )

        assert resp.status_code == 401
        assert loop.wallet.total_balance == Decimal("0")

    def test_missing_signature_rejected(self):
        client, loop, store = _client_with_loop()
        body = _webhook_body(payment_id="http_3", amount="2.00", status="completed")

        resp = client.post("/api/webhooks/payoneer", content=body)

        assert resp.status_code == 401

    def test_missing_secret_env_rejects_everything(self):
        import os

        os.environ.pop("PAYONEER_WEBHOOK_SECRET", None)
        client, loop, store = _client_with_loop()
        body = _webhook_body(payment_id="http_4", amount="2.00", status="completed")
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 503

    def test_malformed_json_rejected(self):
        client, loop, store = _client_with_loop()
        body = b"not json"
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code in (400, 401)

    def test_missing_required_field_rejected(self):
        client, loop, store = _client_with_loop()
        body = _webhook_body(status="completed")

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": _sign(body), "Content-Type": "application/json"},
        )

        assert resp.status_code in (400, 401)

    def test_stale_replay_rejected_with_valid_hmac(self):
        """A webhook older than the freshness window is rejected (401) even
        when its HMAC verifies — acceptance criterion for issue #73."""
        client, loop, store = _client_with_loop()
        stale_ts = time.time() - 600  # 10 minutes old, beyond the 5-min window
        body = json.dumps(
            _event_payload(timestamp=stale_ts, payment_id="stale_1", amount="2.00", status="completed")
        ).encode()
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 401
        assert loop.wallet.total_balance == Decimal("0")

    def test_future_timestamp_rejected(self):
        client, loop, store = _client_with_loop()
        body = json.dumps(
            _event_payload(timestamp=time.time() + 600, payment_id="fut_1", amount="2.00", status="completed")
        ).encode()
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 401
        assert loop.wallet.total_balance == Decimal("0")

    def test_reused_nonce_rejected(self):
        """Replaying an identical (still-fresh) body is rejected on the nonce."""
        client, loop, store = _client_with_loop()
        body = _webhook_body(payment_id="nonce_1", amount="2.00", status="completed")
        sig = _sign(body)
        headers = {"X-Payoneer-Signature": sig, "Content-Type": "application/json"}

        first = client.post("/api/webhooks/payoneer", content=body, headers=headers)
        second = client.post("/api/webhooks/payoneer", content=body, headers=headers)

        assert first.status_code == 200
        assert second.status_code == 401
        assert loop.wallet.total_balance == Decimal("2.00")  # not double-credited

    def test_missing_timestamp_and_nonce_rejected(self):
        """A valid HMAC without a timestamp/nonce cannot be proven fresh."""
        client, loop, store = _client_with_loop()
        body = json.dumps({"payment_id": "no_ts_1", "amount": "2.00", "status": "completed"}).encode()
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 401
        assert loop.wallet.total_balance == Decimal("0")

    def test_redelivery_is_idempotent_over_http(self):
        client, loop, store = _client_with_loop()

        body1 = _webhook_body(payment_id="http_5", amount="9.00", status="completed")
        headers1 = {"X-Payoneer-Signature": _sign(body1), "Content-Type": "application/json"}
        first = client.post("/api/webhooks/payoneer", content=body1, headers=headers1)

        body2 = _webhook_body(payment_id="http_5", amount="9.00", status="completed")
        headers2 = {"X-Payoneer-Signature": _sign(body2), "Content-Type": "application/json"}
        second = client.post("/api/webhooks/payoneer", content=body2, headers=headers2)

        assert first.json()["processed"] is True
        assert second.json()["processed"] is False
        assert loop.wallet.total_balance == Decimal("9.00")

    def test_record_payment_uses_atomic_claim_not_check_then_act(self):
        """record_payment must call try_claim_payment (atomic reserve), not
        the old is_payment_processed-then-mark_payment_processed pattern —
        that pairing has a window where two concurrent deliveries of the
        same completed-payment webhook both pass the check before either
        marks it processed, double-crediting the wallet. This asserts the
        call actually happened rather than just the sequential outcome,
        since a sequential test alone can't distinguish the two designs."""
        client, loop, store = _client_with_loop()
        loop.persistence.try_claim_payment = MagicMock(wraps=loop.persistence.try_claim_payment)

        body = _webhook_body(payment_id="claim_1", amount="9.00", status="completed")
        sig = _sign(body)
        headers = {"X-Payoneer-Signature": sig, "Content-Type": "application/json"}

        client.post("/api/webhooks/payoneer", content=body, headers=headers)

        loop.persistence.try_claim_payment.assert_called_once_with("claim_1")

    def test_loop_not_initialised_returns_503(self):
        from fastapi.testclient import TestClient

        import main as main_mod

        @main_mod.asynccontextmanager
        async def _noop_lifespan(app):
            yield

        test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
        test_app.router.routes.extend(main_mod.app.router.routes)
        main_mod._loop = None

        client = TestClient(test_app)
        body = _webhook_body(payment_id="http_6", amount="2.00", status="completed")
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 503

    def test_manual_confirmation_bypasses_signature_but_requires_token(self):
        client, loop, store = _client_with_loop()
        body = _event_payload(payment_id="manual_1", amount="5.00", status="completed")

        # Without token in header -> 401
        resp = client.post("/api/webhooks/payoneer/manual", json=body)
        assert resp.status_code == 401
        resp = client.post(
            "/api/webhooks/payoneer/manual",
            json=body,
            headers={"Authorization": "Bearer test-token-secure-123"},
        )
        assert resp.status_code == 200
        assert resp.json()["processed"] is True
        assert loop.wallet.total_balance == Decimal("5.00")

    def test_webhook_failure_returns_500_for_provider_redelivery(self):
        client, loop, store = _client_with_loop()

        # The handler must NOT retry in-handler: Payoneer re-delivers, so any
        # transient processing failure returns 5xx after exactly one attempt.
        mock_record = MagicMock(side_effect=Exception("DB error"))
        loop.record_payment = mock_record

        body = _webhook_body(payment_id="retry_1", amount="2.00", status="completed")
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 500
        assert mock_record.call_count == 1


# ---------------------------------------------------------------------------
# Issue #73 — credential separation: no single secret may both confirm
# payments (mint wallet credit) and trigger withdrawals, and neither
# money-movement endpoint may be driven by the generic API_AUTH_TOKEN.
# ---------------------------------------------------------------------------

class TestCredentialSeparation:
    TX = "tx-token-1234567890"
    WD = "wd-token-1234567890"
    API = "api-token-1234567890"

    def _distinct_tokens(self, monkeypatch):
        monkeypatch.setenv("PAYONEER_TX_TOKEN", self.TX)
        monkeypatch.setenv("WITHDRAWAL_TOKEN", self.WD)
        monkeypatch.setenv("API_AUTH_TOKEN", self.API)
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)

    def _funded_client(self):
        from src.wallet import Wallet

        client, loop, _store = _client_with_loop()
        loop.wallet = Wallet(free=Decimal("20.00"))
        return client, loop

    def test_manual_confirm_token_cannot_withdraw(self, monkeypatch):
        self._distinct_tokens(monkeypatch)
        client, _loop = self._funded_client()

        resp = client.post(
            "/api/withdraw",
            json={"pool": "free", "amount": "5.00"},
            headers={"Authorization": f"Bearer {self.TX}"},
        )

        assert resp.status_code == 403
        assert "Invalid" in resp.json()["detail"]

    def test_withdrawal_token_cannot_confirm_payment(self, monkeypatch):
        self._distinct_tokens(monkeypatch)
        client, loop, _store = _client_with_loop()

        body = _event_payload(payment_id="sep_1", amount="5.00", status="completed")
        resp = client.post(
            "/api/webhooks/payoneer/manual",
            json=body,
            headers={"Authorization": f"Bearer {self.WD}"},
        )

        assert resp.status_code == 403
        assert loop.wallet.total_balance == Decimal("0")

    def test_generic_api_token_cannot_drive_money_movement(self, monkeypatch):
        self._distinct_tokens(monkeypatch)
        client, loop, _store = _client_with_loop()

        # API_AUTH_TOKEN must not withdraw…
        resp = client.post(
            "/api/withdraw",
            json={"pool": "free", "amount": "5.00"},
            headers={"Authorization": f"Bearer {self.API}"},
        )
        assert resp.status_code == 403

        # …nor confirm payments.
        body = _event_payload(payment_id="sep_2", amount="5.00", status="completed")
        resp = client.post(
            "/api/webhooks/payoneer/manual",
            json=body,
            headers={"Authorization": f"Bearer {self.API}"},
        )
        assert resp.status_code == 403
        assert loop.wallet.total_balance == Decimal("0")

    def test_each_credential_authorizes_only_its_own_endpoint(self, monkeypatch):
        self._distinct_tokens(monkeypatch)
        client, loop, _store = _client_with_loop()

        body = _event_payload(payment_id="sep_3", amount="5.00", status="completed")
        resp = client.post(
            "/api/webhooks/payoneer/manual",
            json=body,
            headers={"Authorization": f"Bearer {self.TX}"},
        )
        assert resp.status_code == 200
        assert loop.wallet.total_balance == Decimal("5.00")

        client2, loop2 = self._funded_client()
        resp = client2.post(
            "/api/withdraw",
            json={"pool": "free", "amount": "5.00"},
            headers={"Authorization": f"Bearer {self.WD}"},
        )
        assert resp.status_code == 200
        assert loop2.wallet.free == Decimal("15.00")