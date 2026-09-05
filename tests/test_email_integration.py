"""Integration tests for email inbox wiring in main.py."""

from unittest.mock import MagicMock

from src.email_inbox import EmailMessage
from src.persistence import InMemoryStore


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


class TestScanEmailForPaymentAlerts:
    def test_unconfigured_inbox_returns_empty(self, monkeypatch):
        for var in ("EMAIL_IMAP_HOST", "EMAIL_IMAP_USER", "EMAIL_IMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        assert loop.scan_email_for_payment_alerts() == []

    def test_payment_alert_logged_and_marked_read(self, monkeypatch):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        alert = EmailMessage(uid="1", sender="alerts@payoneer.com", subject="Payout sent", body="")
        mock_inbox = MagicMock()
        mock_inbox.fetch_unread.return_value = [alert]
        loop.email_inbox = mock_inbox

        found = loop.scan_email_for_payment_alerts()

        assert found == [{"sender": "alerts@payoneer.com", "subject": "Payout sent"}]
        mock_inbox.mark_as_read.assert_called_once_with("1")
        assert any("Payment alert email" in e for e in loop._event_log)

    def test_non_payment_email_ignored(self, monkeypatch):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        unrelated = EmailMessage(uid="1", sender="news@example.com", subject="Digest", body="")
        mock_inbox = MagicMock()
        mock_inbox.fetch_unread.return_value = [unrelated]
        loop.email_inbox = mock_inbox

        found = loop.scan_email_for_payment_alerts()

        assert found == []
        mock_inbox.mark_as_read.assert_not_called()

    def test_fetch_failure_does_not_raise(self):
        import main as main_mod

        loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
        mock_inbox = MagicMock()
        mock_inbox.fetch_unread.side_effect = RuntimeError("IMAP down")
        loop.email_inbox = mock_inbox

        assert loop.scan_email_for_payment_alerts() == []


class TestEmailEndpoints:
    def test_status_endpoint_reports_unconfigured(self, monkeypatch):
        for var in ("EMAIL_IMAP_HOST", "EMAIL_IMAP_USER", "EMAIL_IMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        client, _loop, _store = _client_with_loop()

        resp = client.get("/api/email/status")

        assert resp.status_code == 200
        assert resp.json() == {"configured": False}

    def test_scan_endpoint_returns_alerts(self):
        client, loop, _store = _client_with_loop()
        alert = EmailMessage(uid="1", sender="alerts@payoneer.com", subject="Payout sent", body="")
        mock_inbox = MagicMock()
        mock_inbox.fetch_unread.return_value = [alert]
        loop.email_inbox = mock_inbox

        resp = client.post("/api/email/scan", headers={"Authorization": "Bearer test-token"})

        assert resp.status_code == 200
        assert resp.json()["alerts_found"] == [{"sender": "alerts@payoneer.com", "subject": "Payout sent"}]

    def test_status_endpoint_503_when_loop_not_initialised(self):
        import main as main_mod

        main_mod._loop = None
        from fastapi.testclient import TestClient

        client = TestClient(main_mod.app)
        resp = client.get("/api/email/status")

        assert resp.status_code == 503

    def test_scan_endpoint_503_when_loop_not_initialised(self):
        import main as main_mod

        main_mod._loop = None
        from fastapi.testclient import TestClient

        client = TestClient(main_mod.app)
        resp = client.post("/api/email/scan", headers={"Authorization": "Bearer test-token"})

        assert resp.status_code == 503
