"""Integration tests for the /api/webhooks/payoneer endpoint and
SurvivalLoop.record_payment (§20 payment confirmation).
"""

import hashlib
import hmac
import json
from decimal import Decimal

from src.payoneer_webhook import parse_webhook_payload
from src.persistence import InMemoryStore

SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


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
        event = parse_webhook_payload(
            {"payment_id": "pay_1", "amount": "3.00", "status": "completed"}
        )

        result = loop.record_payment(event)

        assert result["processed"] is True
        assert loop.wallet.total_balance == Decimal("3.00")
        assert store.is_payment_processed("pay_1") is True

    def test_completed_payment_repays_debt_first(self):
        import main as main_mod

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        loop.wallet.debt = Decimal("2.00")
        event = parse_webhook_payload(
            {"payment_id": "pay_2", "amount": "5.00", "status": "completed"}
        )

        result = loop.record_payment(event)

        assert result["debt_repaid"] == "2.00"
        assert result["to_free"] == "3.00"
        assert loop.wallet.locked == Decimal("2.00")
        assert loop.wallet.free == Decimal("3.00")

    def test_duplicate_payment_id_not_credited_twice(self):
        import main as main_mod

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        event = parse_webhook_payload(
            {"payment_id": "pay_3", "amount": "4.00", "status": "completed"}
        )

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
        event = parse_webhook_payload(
            {"payment_id": "pay_4", "amount": "4.00", "status": "pending"}
        )

        result = loop.record_payment(event)

        assert result["processed"] is False
        assert loop.wallet.total_balance == Decimal("0")
        assert store.is_payment_processed("pay_4") is False

    def test_persists_state_after_credit(self):
        import main as main_mod

        store = InMemoryStore()
        loop = main_mod.SurvivalLoop(persistence=store)
        event = parse_webhook_payload(
            {"payment_id": "pay_5", "amount": "1.50", "status": "completed"}
        )

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
        body = json.dumps({"payment_id": "http_1", "amount": "2.00", "status": "completed"}).encode()
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
        body = json.dumps({"payment_id": "http_2", "amount": "2.00", "status": "completed"}).encode()

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": "deadbeef", "Content-Type": "application/json"},
        )

        assert resp.status_code == 401
        assert loop.wallet.total_balance == Decimal("0")

    def test_missing_signature_rejected(self):
        client, loop, store = _client_with_loop()
        body = json.dumps({"payment_id": "http_3", "amount": "2.00", "status": "completed"}).encode()

        resp = client.post("/api/webhooks/payoneer", content=body)

        assert resp.status_code == 401

    def test_missing_secret_env_rejects_everything(self):
        import os

        os.environ.pop("PAYONEER_WEBHOOK_SECRET", None)
        client, loop, store = _client_with_loop()
        body = json.dumps({"payment_id": "http_4", "amount": "2.00", "status": "completed"}).encode()
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

        assert resp.status_code == 400

    def test_missing_required_field_rejected(self):
        client, loop, store = _client_with_loop()
        body = json.dumps({"status": "completed"}).encode()
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 400

    def test_redelivery_is_idempotent_over_http(self):
        client, loop, store = _client_with_loop()
        body = json.dumps({"payment_id": "http_5", "amount": "9.00", "status": "completed"}).encode()
        sig = _sign(body)
        headers = {"X-Payoneer-Signature": sig, "Content-Type": "application/json"}

        first = client.post("/api/webhooks/payoneer", content=body, headers=headers)
        second = client.post("/api/webhooks/payoneer", content=body, headers=headers)

        assert first.json()["processed"] is True
        assert second.json()["processed"] is False
        assert loop.wallet.total_balance == Decimal("9.00")

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
        body = json.dumps({"payment_id": "http_6", "amount": "2.00", "status": "completed"}).encode()
        sig = _sign(body)

        resp = client.post(
            "/api/webhooks/payoneer",
            content=body,
            headers={"X-Payoneer-Signature": sig, "Content-Type": "application/json"},
        )

        assert resp.status_code == 503
