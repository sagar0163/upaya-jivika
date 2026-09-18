"""Tests for src/api_auth.py — bearer-token guard on mutating endpoints."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api_auth import require_api_token


@pytest.fixture
def app():
    app = FastAPI()

    @app.post("/protected", dependencies=[Depends(require_api_token)])
    async def protected():
        return {"ok": True}

    return app


class TestRequireApiToken:
    def test_missing_env_var_fails_closed(self, app, monkeypatch):
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        client = TestClient(app)

        resp = client.post("/protected", headers={"Authorization": "Bearer anything"})

        assert resp.status_code == 503

    def test_missing_header_rejected(self, app):
        client = TestClient(app)

        resp = client.post("/protected")

        assert resp.status_code == 401

    def test_malformed_header_rejected(self, app):
        client = TestClient(app)

        resp = client.post("/protected", headers={"Authorization": "test-token-secure-123"})

        assert resp.status_code == 401

    def test_wrong_token_rejected(self, app):
        client = TestClient(app)

        resp = client.post("/protected", headers={"Authorization": "Bearer wrong"})

        assert resp.status_code == 403

    def test_correct_token_accepted(self, app):
        client = TestClient(app)

        resp = client.post("/protected", headers={"Authorization": "Bearer test-token-secure-123"})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_correct_session_cookie_accepted(self, app):
        client = TestClient(app)
        client.cookies.set("uj_session", "test-token-secure-123")

        resp = client.post("/protected")

        assert resp.status_code == 200

    def test_wrong_session_cookie_rejected(self, app):
        client = TestClient(app)
        client.cookies.set("uj_session", "wrong")

        resp = client.post("/protected")

        assert resp.status_code == 403

    def test_header_takes_precedence_over_cookie(self, app):
        client = TestClient(app)
        client.cookies.set("uj_session", "wrong")

        resp = client.post("/protected", headers={"Authorization": "Bearer test-token-secure-123"})

        assert resp.status_code == 200


class TestAuthFailureThrottling:
    def _no_backoff(self, monkeypatch):
        import src.api_auth as api_auth

        async def _noop():
            return None

        monkeypatch.setattr(api_auth, "_auth_failure_backoff", _noop)

    def test_weak_token_fails_closed(self, app, monkeypatch):
        monkeypatch.setenv("API_AUTH_TOKEN", "short")
        client = TestClient(app)

        resp = client.post("/protected", headers={"Authorization": "Bearer short"})

        assert resp.status_code == 503

    def test_repeated_failures_lock_out_ip(self, app, monkeypatch):
        self._no_backoff(monkeypatch)
        client = TestClient(app)

        for _ in range(5):
            resp = client.post("/protected", headers={"Authorization": "Bearer wrong"})
            assert resp.status_code == 403

        # Even a correct token is rejected once the IP is locked out.
        resp = client.post("/protected", headers={"Authorization": "Bearer test-token-secure-123"})
        assert resp.status_code == 429

    def test_lockout_expires_after_failure_window(self, app, monkeypatch):
        self._no_backoff(monkeypatch)
        import src.api_auth as api_auth

        clock = {"now": 1_000_000.0}
        monkeypatch.setattr(api_auth, "_now", lambda: clock["now"])
        client = TestClient(app)

        for _ in range(5):
            client.post("/protected", headers={"Authorization": "Bearer wrong"})
        assert client.post("/protected", headers={"Authorization": "Bearer test-token-secure-123"}).status_code == 429

        # Advance past the failure window: the IP is unlocked and a correct
        # token succeeds again.
        clock["now"] += api_auth._FAILURE_WINDOW + 1
        resp = client.post("/protected", headers={"Authorization": "Bearer test-token-secure-123"})
        assert resp.status_code == 200

    def test_few_failures_do_not_lock_out(self, app, monkeypatch):
        self._no_backoff(monkeypatch)
        client = TestClient(app)

        for _ in range(2):
            client.post("/protected", headers={"Authorization": "Bearer wrong"})

        resp = client.post("/protected", headers={"Authorization": "Bearer test-token-secure-123"})
        assert resp.status_code == 200
