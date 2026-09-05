"""Integration tests for POST /api/withdraw and SurvivalLoop.process_withdrawal."""

from decimal import Decimal

from src.persistence import InMemoryStore
from src.wallet import Wallet


def _client_with_loop(store=None):
    """Mirrors the pattern in tests/test_payoneer_webhook_integration.py."""
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


class TestProcessWithdrawal:
    def test_withdraws_from_free_pool(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        loop.wallet = Wallet(free=Decimal("20.00"))

        from src.withdrawal import WithdrawalPool

        result = loop.process_withdrawal(WithdrawalPool.FREE, Decimal("5.00"))

        assert loop.wallet.free == Decimal("15.00")
        assert result["pool"] == "free"
        assert result["amount"] == "5.00"


class TestWithdrawEndpoint:
    def test_valid_free_withdrawal(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        client, loop, _store = _client_with_loop()
        loop.wallet = Wallet(free=Decimal("20.00"))

        resp = client.post(
            "/api/withdraw",
            json={"pool": "free", "amount": "5.00"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["pool"] == "free"
        assert body["amount"] == "5.00"
        assert loop.wallet.free == Decimal("15.00")

    def test_valid_locked_withdrawal(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        client, loop, _store = _client_with_loop()
        loop.wallet = Wallet(locked=Decimal("20.00"))

        resp = client.post(
            "/api/withdraw",
            json={"pool": "locked", "amount": "5.00"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        assert loop.wallet.locked == Decimal("15.00")

    def test_insufficient_balance_returns_400(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        client, loop, _store = _client_with_loop()
        loop.wallet = Wallet(free=Decimal("2.00"))

        resp = client.post(
            "/api/withdraw",
            json={"pool": "free", "amount": "5.00"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 400

    def test_negative_amount_returns_400(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        client, loop, _store = _client_with_loop()
        loop.wallet = Wallet(free=Decimal("20.00"))

        resp = client.post(
            "/api/withdraw",
            json={"pool": "free", "amount": "-5.00"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 400

    def test_invalid_pool_returns_400(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        client, _loop, _store = _client_with_loop()

        resp = client.post(
            "/api/withdraw",
            json={"pool": "vault", "amount": "5.00"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 400

    def test_missing_amount_returns_400(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        client, _loop, _store = _client_with_loop()

        resp = client.post(
            "/api/withdraw",
            json={"pool": "free"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 400

    def test_malformed_json_returns_400(self, monkeypatch):
        monkeypatch.delenv("PAYONEER_API_KEY", raising=False)
        client, _loop, _store = _client_with_loop()

        resp = client.post(
            "/api/withdraw",
            content=b"not json",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 400

    def test_loop_not_initialised_returns_503(self):
        from fastapi.testclient import TestClient

        import main as main_mod

        main_mod._loop = None
        client = TestClient(main_mod.app)

        resp = client.post(
            "/api/withdraw",
            json={"pool": "free", "amount": "5.00"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 503
