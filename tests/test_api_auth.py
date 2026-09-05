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

        resp = client.post("/protected", headers={"Authorization": "test-token"})

        assert resp.status_code == 401

    def test_wrong_token_rejected(self, app):
        client = TestClient(app)

        resp = client.post("/protected", headers={"Authorization": "Bearer wrong"})

        assert resp.status_code == 403

    def test_correct_token_accepted(self, app):
        client = TestClient(app)

        resp = client.post("/protected", headers={"Authorization": "Bearer test-token"})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
