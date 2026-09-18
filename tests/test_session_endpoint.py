"""Tests for POST /api/session and /api/session/logout — the httpOnly
cookie exchange that lets the dashboard authenticate without ever holding
the raw API token in page-readable storage (see src/api_auth.py).
"""

import os

from fastapi.testclient import TestClient

import main as main_mod


def _client():
    @main_mod.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
    test_app.router.routes.extend(main_mod.app.router.routes)
    return TestClient(test_app)


class TestCreateSession:
    def setup_method(self):
        self._old = os.environ.get("API_AUTH_TOKEN")
        os.environ["API_AUTH_TOKEN"] = "test-token-secure-123"

    def teardown_method(self):
        if self._old is None:
            os.environ.pop("API_AUTH_TOKEN", None)
        else:
            os.environ["API_AUTH_TOKEN"] = self._old

    def test_correct_token_sets_httponly_cookie(self):
        client = _client()

        resp = client.post("/api/session", json={"token": "test-token-secure-123"})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        cookie = resp.cookies.get("uj_session")
        assert cookie == "test-token-secure-123"
        set_cookie_header = resp.headers.get("set-cookie", "")
        assert "httponly" in set_cookie_header.lower()
        assert "samesite=strict" in set_cookie_header.lower()

    def test_secure_flag_set_behind_https_proxy(self):
        """Render terminates TLS at the edge and forwards plain HTTP, so
        request.url.scheme reads "http" in-process — the Secure flag must
        come from X-Forwarded-Proto instead, or every production cookie
        silently loses the Secure attribute."""
        client = _client()

        resp = client.post(
            "/api/session",
            json={"token": "test-token-secure-123"},
            headers={"X-Forwarded-Proto": "https"},
        )

        assert resp.status_code == 200
        assert "secure" in resp.headers.get("set-cookie", "").lower()

    def test_wrong_token_rejected(self):
        client = _client()

        resp = client.post("/api/session", json={"token": "wrong"})

        assert resp.status_code == 403
        assert "uj_session" not in resp.cookies

    def test_missing_token_rejected(self):
        client = _client()

        resp = client.post("/api/session", json={})

        assert resp.status_code == 403

    def test_malformed_body_rejected(self):
        client = _client()

        resp = client.post("/api/session", content=b"not json", headers={"Content-Type": "application/json"})

        assert resp.status_code == 400

    def test_cookie_then_protected_endpoint_succeeds(self):
        client = _client()
        session_resp = client.post("/api/session", json={"token": "test-token-secure-123"})
        assert session_resp.status_code == 200

        resp = client.post("/api/debt/tick")

        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_missing_secret_env_rejects_everything(self):
        os.environ.pop("API_AUTH_TOKEN", None)
        client = _client()

        resp = client.post("/api/session", json={"token": "anything"})

        assert resp.status_code == 503


class TestLogoutSession:
    def setup_method(self):
        self._old = os.environ.get("API_AUTH_TOKEN")
        os.environ["API_AUTH_TOKEN"] = "test-token-secure-123"

    def teardown_method(self):
        if self._old is None:
            os.environ.pop("API_AUTH_TOKEN", None)
        else:
            os.environ["API_AUTH_TOKEN"] = self._old

    def test_logout_clears_cookie_and_revokes_access(self):
        client = _client()
        client.post("/api/session", json={"token": "test-token-secure-123"})

        logout_resp = client.post("/api/session/logout")
        assert logout_resp.status_code == 200

        resp = client.post("/api/debt/tick")
        assert resp.status_code == 401
