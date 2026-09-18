"""Shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def api_auth_token(monkeypatch):
    """Give every test a stable API_AUTH_TOKEN so protected endpoints are
    testable without each test file wiring its own secret. Tests exercising
    a protected endpoint still need to send ``Authorization: Bearer test-token-secure-123``
    explicitly — this fixture only ensures the dependency has something to
    check against instead of failing closed with a 503.
    """
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token-secure-123")
    monkeypatch.setenv("PAYONEER_TX_TOKEN", "test-token-secure-123")
    monkeypatch.setenv("WITHDRAWAL_TOKEN", "test-token-secure-123")


@pytest.fixture(autouse=True)
def clear_rate_limit():
    from src.api_auth import _failed_attempts
    _failed_attempts.clear()
