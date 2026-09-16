"""Tests for issue #74 — the now-gated read surface.

The following endpoints must refuse unauthenticated requests (401 missing /
403 invalid) and succeed with a valid token:

    /status, /ws, /api/spend/pending, /api/email/status,
    /api/survival-mode (GET)

Additionally /health must stay public but must never leak economic state,
and the dashboard shell (/) must remain publicly reachable.

WebSocket tests use starlette's TestClient; rejected handshakes are accepted
first (sending a real close frame with 4401/4403) so browsers can distinguish
"auth required" from "server down".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main as main_mod
from src.persistence import InMemoryStore

#: conftest.py sets API_AUTH_TOKEN=test-token for every test.
_TOKEN = "test-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
InvalidToken = "not-the-token"


def _make_app_and_loop() -> tuple:
    """Fresh app (no real scheduler) wired to the real route table, plus a
    fresh SurvivalLoop pointed at module-level ``_loop``."""
    @main_mod.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    test_app = main_mod.FastAPI(title="test", lifespan=_noop_lifespan)
    test_app.router.routes.extend(main_mod.app.router.routes)
    loop = main_mod.SurvivalLoop(persistence=InMemoryStore())
    main_mod._loop = loop
    return test_app, loop


@pytest.fixture(autouse=True)
def _clean_loop():
    """Ensure no module-level loop leaks across tests."""
    yield
    main_mod._loop = None


# ---------------------------------------------------------------------------
# / — public dashboard shell
# ---------------------------------------------------------------------------

class TestDashboardShell:
    def test_dashboard_serves_html_publicly(self):
        """The dashboard HTML stays public (it is the login-prompt surface);
        it carries no state — all data flows through the now-gated endpoints."""
        test_app, _loop = _make_app_and_loop()
        resp = TestClient(test_app).get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "upaya-jivika" in resp.text


# ---------------------------------------------------------------------------
# /health — public, bare liveness
# ---------------------------------------------------------------------------

class TestHealthLiveness:
    def test_public_200_with_loop(self):
        test_app, _loop = _make_app_and_loop()
        resp = TestClient(test_app).get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_public_200_without_loop(self):
        test_app, _loop = _make_app_and_loop()
        main_mod._loop = None
        resp = TestClient(test_app).get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "initialising"}

    def test_health_leaks_no_state(self):
        """No debt / life / earnings / research-trigger fields (issue #74)."""
        test_app, _loop = _make_app_and_loop()
        _loop.debt_tick()
        body = TestClient(test_app).get("/health").json()
        assert set(body.keys()) == {"status"}
        assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# /status — gated
# ---------------------------------------------------------------------------

class TestStatusAuth:
    def test_unauthenticated_returns_401(self):
        test_app, _loop = _make_app_and_loop()
        assert TestClient(test_app).get("/status").status_code == 401

    def test_wrong_token_returns_403(self):
        test_app, _loop = _make_app_and_loop()
        resp = TestClient(test_app).get("/status", headers={"Authorization": f"Bearer {InvalidToken}"})
        assert resp.status_code == 403

    def test_valid_token_returns_200(self):
        test_app, _loop = _make_app_and_loop()
        resp = TestClient(test_app).get("/status", headers=_AUTH)
        assert resp.status_code == 200
        assert "debt" in resp.json()


# ---------------------------------------------------------------------------
# /api/spend/pending — gated
# ---------------------------------------------------------------------------

class TestPendingSpendAuth:
    def test_unauthenticated_returns_401(self):
        test_app, _loop = _make_app_and_loop()
        assert TestClient(test_app).get("/api/spend/pending").status_code == 401

    def test_valid_token_returns_200(self):
        test_app, _loop = _make_app_and_loop()
        resp = TestClient(test_app).get("/api/spend/pending", headers=_AUTH)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/email/status — gated
# ---------------------------------------------------------------------------

class TestEmailStatusAuth:
    def test_unauthenticated_returns_401(self):
        test_app, _loop = _make_app_and_loop()
        assert TestClient(test_app).get("/api/email/status").status_code == 401

    def test_valid_token_returns_200(self):
        test_app, _loop = _make_app_and_loop()
        resp = TestClient(test_app).get("/api/email/status", headers=_AUTH)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/survival-mode (GET) — gated
# ---------------------------------------------------------------------------

class TestSurvivalModeAuth:
    def test_unauthenticated_returns_401(self):
        test_app, _loop = _make_app_and_loop()
        assert TestClient(test_app).get("/api/survival-mode").status_code == 401

    def test_valid_token_returns_200(self):
        test_app, _loop = _make_app_and_loop()
        resp = TestClient(test_app).get("/api/survival-mode", headers=_AUTH)
        assert resp.status_code == 200
        assert "enabled" in resp.json()


# ---------------------------------------------------------------------------
# /ws — gated (accept-then-close with 4401 / 4403)
# ---------------------------------------------------------------------------

class TestWebSocketAuth:
    def test_missing_token_rejected_with_4401(self):
        """Connection closes with a custom 4401 close code on missing auth."""
        test_app, _loop = _make_app_and_loop()
        with TestClient(test_app).websocket_connect("/ws") as ws:
            close = ws.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4401

    def test_invalid_token_rejected_with_4403(self):
        test_app, _loop = _make_app_and_loop()
        with TestClient(test_app).websocket_connect(
            f"/ws?token={InvalidToken}"
        ) as ws:
            close = ws.receive()
        assert close["type"] == "websocket.close"
        assert close["code"] == 4403

    def test_valid_query_param_token_succeeds(self):
        test_app, _loop = _make_app_and_loop()
        with TestClient(test_app).websocket_connect(f"/ws?token={_TOKEN}") as ws:
            msg = ws.receive_json()
            assert msg["event"] == "status_snapshot"

    def test_valid_cookie_token_succeeds(self):
        test_app, _loop = _make_app_and_loop()
        client = TestClient(test_app)
        client.cookies.set(main_mod.SESSION_COOKIE, _TOKEN)
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["event"] == "status_snapshot"

    def test_valid_bearer_header_token_succeeds(self):
        test_app, _loop = _make_app_and_loop()
        with TestClient(test_app).websocket_connect(
            "/ws", headers={"Authorization": f"Bearer {_TOKEN}"}
        ) as ws:
            msg = ws.receive_json()
            assert msg["event"] == "status_snapshot"
