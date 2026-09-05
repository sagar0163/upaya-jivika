"""Integration tests for the human approval gate wired into main.py (§14)."""

from datetime import timedelta
from decimal import Decimal

from src.approval_gate import ApprovalGate, SpendDecision
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


class TestRequestAiSpend:
    def test_small_spend_executes_immediately(self):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        loop.wallet = Wallet(free=Decimal("10.00"))

        result = loop.request_ai_spend(Decimal("1.00"), Decimal("0.99"), "small experiment")

        assert result["status"] == "executed_immediately"
        assert loop.wallet.free == Decimal("9.00")

    def test_large_spend_creates_pending_and_does_not_debit_yet(self):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        loop.wallet = Wallet(free=Decimal("100.00"))

        result = loop.request_ai_spend(Decimal("5.00"), Decimal("0.97"), "2captcha solve")

        assert result["status"] == "pending"
        assert loop.wallet.free == Decimal("100.00")
        assert len(loop.approval_gate.list_pending()) == 1

    def test_large_spend_raises_alert(self):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        loop.wallet = Wallet(free=Decimal("100.00"))

        loop.request_ai_spend(Decimal("5.00"), Decimal("0.97"), "2captcha solve")

        assert any("AI wants to spend" in a.message for a in loop.alerts.alerts())


class TestRejectPendingSpend:
    def test_reject_prevents_future_execution(self):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        loop.wallet = Wallet(free=Decimal("100.00"))
        result = loop.request_ai_spend(Decimal("5.00"), Decimal("0.97"), "reason")

        assert loop.reject_pending_spend(result["spend_id"]) is True

        # A rejected spend resolves as REJECTED even before its deadline.
        loop.resolve_pending_spends()
        assert loop.wallet.free == Decimal("100.00")

    def test_reject_unknown_spend_returns_false(self):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        assert loop.reject_pending_spend("nonexistent") is False


class TestResolvePendingSpends:
    def test_expired_spend_auto_executes(self):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        loop.wallet = Wallet(free=Decimal("100.00"))
        loop.approval_gate = ApprovalGate(loop.persistence, window=timedelta(hours=-1))
        loop.request_ai_spend(Decimal("5.00"), Decimal("0.97"), "reason")

        results = loop.resolve_pending_spends()

        assert len(results) == 1
        assert results[0]["decision"] == SpendDecision.AUTO_APPROVED.value
        assert loop.wallet.free == Decimal("95.00")

    def test_unexpired_spend_not_resolved(self):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        loop.wallet = Wallet(free=Decimal("100.00"))
        loop.request_ai_spend(Decimal("5.00"), Decimal("0.97"), "reason")

        assert loop.resolve_pending_spends() == []
        assert loop.wallet.free == Decimal("100.00")


class TestSpendEndpoints:
    def test_pending_endpoint_lists_requests(self):
        client, loop, _store = _client_with_loop()
        loop.wallet = Wallet(free=Decimal("100.00"))
        loop.request_ai_spend(Decimal("5.00"), Decimal("0.97"), "reason")

        resp = client.get("/api/spend/pending")

        assert resp.status_code == 200
        assert len(resp.json()["pending"]) == 1

    def test_reject_endpoint_success(self):
        client, loop, _store = _client_with_loop()
        loop.wallet = Wallet(free=Decimal("100.00"))
        result = loop.request_ai_spend(Decimal("5.00"), Decimal("0.97"), "reason")

        resp = client.post(f"/api/spend/{result['spend_id']}/reject")

        assert resp.status_code == 200
        assert resp.json()["rejected"] is True

    def test_reject_endpoint_404_for_unknown_id(self):
        client, _loop, _store = _client_with_loop()

        resp = client.post("/api/spend/nonexistent/reject")

        assert resp.status_code == 404

    def test_pending_endpoint_503_when_loop_not_initialised(self):
        from fastapi.testclient import TestClient

        import main as main_mod

        main_mod._loop = None
        client = TestClient(main_mod.app)

        resp = client.get("/api/spend/pending")

        assert resp.status_code == 503
