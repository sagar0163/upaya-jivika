"""Integration tests for issue #75 revenue-split API endpoints + status payload."""

from decimal import Decimal

from src.persistence import InMemoryStore
from src.wallet import Wallet


def _client_with_loop(store=None):
    """Mirrors the pattern in tests/test_withdrawal_integration.py."""
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


REVENUE_SPLIT_BODY = {
    "tiers": [
        {"min_balance": "0", "fractions": {"owner": "0", "reinvest": "1", "reserve": "0"}},
        {"min_balance": "50", "fractions": {"owner": "0.1", "reinvest": "0.8", "reserve": "0.1"}},
        {"min_balance": "200", "fractions": {"owner": "0.3", "reinvest": "0.5", "reserve": "0.2"}},
    ],
    "auto_payout_enabled": True,
    "auto_payout_minimum": "10.00",
    "auto_payout_cadence_hours": 24,
    "seed_capital_repaid": "0.00",
}


class TestPolicyEndpoint:
    def test_get_default_policy(self, monkeypatch):
        client, _loop, _store = _client_with_loop()
        resp = client.get("/api/revenue-split/policy", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["tiers"]) == 1
        assert body["tiers"][0]["min_balance"] == "0"

    def test_set_policy_and_persist(self, monkeypatch):
        client, loop, store = _client_with_loop()
        resp = client.post(
            "/api/revenue-split/policy",
            json=REVENUE_SPLIT_BODY,
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["tiers"]) == 3

        # Persisted in the store
        raw = store.load_revenue_split_policy()
        assert raw is not None
        assert len(raw["tiers"]) == 3

        # Loop's engine refreshed
        assert len(loop.revenue_split_policy.tiers) == 3

    def test_set_invalid_policy_returns_400(self, monkeypatch):
        client, _loop, _store = _client_with_loop()
        resp = client.post(
            "/api/revenue-split/policy",
            json={"tiers": [{"min_balance": "not-a-number", "fractions": {}}]},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 400

    def test_policy_survives_restart(self, monkeypatch):
        store = InMemoryStore()
        client, _loop, _store = _client_with_loop(store)
        resp = client.post(
            "/api/revenue-split/policy",
            json=REVENUE_SPLIT_BODY,
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200

        # New loop on same store picks up the policy
        import main as main_mod
        loop2 = main_mod.SurvivalLoop(persistence=store)
        assert len(loop2.revenue_split_policy.tiers) == 3
        assert loop2.revenue_split_policy.auto_payout_enabled is True


class TestPayoutsEndpoint:
    def test_payout_history_empty(self, monkeypatch):
        client, _loop, _store = _client_with_loop()
        resp = client.get("/api/revenue-split/payouts", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        assert resp.json()["payouts"] == []
        assert resp.json()["seed_capital_repaid"] == "0.00"

    def test_trigger_below_minimum_returns_not_triggered(self, monkeypatch):
        store = InMemoryStore()
        client, loop, _store = _client_with_loop(store)
        loop.wallet = Wallet(free=Decimal("0"), owner_owed=Decimal("0"))

        resp = client.post(
            "/api/revenue-split/payouts/trigger",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["triggered"] is False


class TestStatusPayload:
    def test_status_includes_revenue_split(self, monkeypatch):
        client, loop, store = _client_with_loop()
        loop.wallet = Wallet(free=Decimal("250.00"), owner_owed=Decimal("5.00"))

        resp = client.get("/status", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["wallet_owner_owed"] == "5.00"
        assert "revenue_split" in body
        rs = body["revenue_split"]
        assert rs["owner_owed"] == "5.00"
        assert rs["policy"]["tiers"][0]["min_balance"] == "0"
        assert "seed_capital_repaid" in rs["policy"]


class TestRecordPaymentSplitIntegration:
    def test_payment_confirmed_applies_split(self, monkeypatch):
        """record_payment must split free-pool earnings per the policy."""
        from src.payoneer_webhook import PayoneerWebhookEvent

        store = InMemoryStore()
        client, loop, _store = _client_with_loop(store)

        # Configure a 2-tier policy via the API first
        resp = client.post(
            "/api/revenue-split/policy",
            json=REVENUE_SPLIT_BODY,
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200

        loop.wallet = Wallet()

        event = PayoneerWebhookEvent(
            payment_id="pay_test_1",
            amount=Decimal("100.00"),
            currency="USD",
            status="completed",
        )

        result = loop.record_payment(event)

        assert result["processed"] is True
        assert result["split"]["owner"] == "10.00"
        assert result["split"]["reinvest"] == "80.00"
        assert result["split"]["reserve"] == "10.00"
        assert loop.wallet.free == Decimal("80.00")
        assert loop.wallet.locked == Decimal("10.00")
        assert loop.wallet.owner_owed == Decimal("10.00")

        # Audit trail: processed payment carries the split
        processed = store._processed_payments["pay_test_1"]
        assert processed["split"]["owner"] == "10.00"

    def test_payment_confirmed_with_debt_repayment_skips_split_of_lock(self, monkeypatch):
        """Debt repayment to locked pool is not revenue — no split on it."""
        from src.payoneer_webhook import PayoneerWebhookEvent

        store = InMemoryStore()
        client, loop, _store = _client_with_loop(store)
        resp = client.post(
            "/api/revenue-split/policy",
            json=REVENUE_SPLIT_BODY,
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200

        loop.wallet = Wallet(debt=Decimal("100.00"))

        event = PayoneerWebhookEvent(
            payment_id="pay_test_debt",
            amount=Decimal("100.00"),
            currency="USD",
            status="completed",
        )
        result = loop.record_payment(event)

        # 100 debt repaid -> to_free=0 -> no split recorded
        assert result["processed"] is True
        assert result["split"] is None
        assert loop.wallet.locked == Decimal("100.00")
        assert loop.wallet.owner_owed == Decimal("0.00")


class TestOwnerPayoutIntegration:
    def test_owner_payout_via_trigger(self, monkeypatch):
        """check_owner_payout: above minimum + enabled -> recorded payout."""

        store = InMemoryStore()
        client, loop, _store = _client_with_loop(store)

        # Enable auto-payout with tier 100% owner via policy
        policy_body = {
            "tiers": [
                {"min_balance": "0", "fractions": {"owner": "1", "reinvest": "0", "reserve": "0"}}
            ],
            "auto_payout_enabled": True,
            "auto_payout_minimum": "10.00",
            "auto_payout_cadence_hours": 24,
            "seed_capital_repaid": "0.00",
        }
        resp = client.post(
            "/api/revenue-split/policy",
            json=policy_body,
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200

        loop.wallet = Wallet(free=Decimal("0"), owner_owed=Decimal("25.00"))

        record = loop.check_owner_payout()
        assert record is not None
        assert record.completed is True
        assert loop.wallet.owner_owed == Decimal("0.00")
        assert loop.wallet.free == Decimal("0.00")

        # Audit + cold archive
        payout_records = loop.get_payout_records()
        assert len(payout_records) == 1
        assert payout_records[0].amount == Decimal("25.00")

    def test_owner_payout_idempotent_via_repeated_trigger(self, monkeypatch):
        """Repeated check_owner_payout within cadence does not double-pay."""

        store = InMemoryStore()
        client, loop, _store = _client_with_loop(store)
        policy_body = {
            "tiers": [
                {"min_balance": "0", "fractions": {"owner": "1", "reinvest": "0", "reserve": "0"}}
            ],
            "auto_payout_enabled": True,
            "auto_payout_minimum": "10.00",
            "auto_payout_cadence_hours": 24,
            "seed_capital_repaid": "0.00",
        }
        client.post("/api/revenue-split/policy", json=policy_body, headers={"Authorization": "Bearer test-token"})

        loop.wallet = Wallet(free=Decimal("0"), owner_owed=Decimal("25.00"))

        first = loop.check_owner_payout()
        assert first is not None and first.completed

        # Re-accumulate and re-trigger immediately — cadence blocks it
        loop.wallet.owner_owed = Decimal("20.00")
        second = loop.check_owner_payout()
        assert second is None
        assert loop.wallet.owner_owed == Decimal("20.00")
        assert len(loop.get_payout_records()) == 1