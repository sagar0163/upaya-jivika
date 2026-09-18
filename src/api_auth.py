"""Bearer-token guard for mutating API endpoints.

The dashboard is intentionally public (artifact.md §1/§18 — anyone can
watch the agent live), but that means every state-changing endpoint
(withdraw, veto a pending spend, trigger a manual debt tick/research
cycle) is reachable by anyone who finds the URL unless it's separately
gated. This mirrors the fail-closed pattern already used for the Payoneer
webhook: if ``API_AUTH_TOKEN`` isn't configured, every protected request
is rejected rather than silently accepted.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import time

from fastapi import HTTPException, Request

AUTH_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "

#: Name of the httpOnly session cookie set by ``POST /api/session``. Using a
#: cookie the page's own JS can never read (rather than ``localStorage``)
#: means a future XSS on the dashboard can't steal the token by reading
#: browser storage — the cookie is invisible to page script either way.
SESSION_COOKIE = "uj_session"

# --- Auth-failure throttling -------------------------------------------------
# The token guard is a plain string comparison, so an attacker who can reach
# an endpoint could otherwise brute-force the token over the network. Throttle
# per client IP: after ``_MAX_FAILURES`` failed attempts inside
# ``_FAILURE_WINDOW`` seconds the IP is temporarily locked out (429), and
# every failed comparison additionally pays a small ``_AUTH_FAILURE_BACKOFF``
# sleep so guessing the correct token is slow even before the lockout hits.
_MAX_FAILURES = 5
_FAILURE_WINDOW = 60  # seconds
_AUTH_FAILURE_BACKOFF = 0.5  # seconds
#: Hard cap on the number of tracked IPs; a token-scanning botnet must not be
#: able to balloon this dict. If it overflows, the in-memory store is reset.
_MAX_TRACKED_IPS = 5_000

_failed_attempts: dict[str, list[float]] = {}


def _now() -> float:
    """Wall-clock source, isolated so tests can advance a fake clock."""
    return time.time()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    """Raise 429 if this client IP has failed auth too many times recently."""
    client_ip = _client_ip(request)
    now = _now()
    attempts = [t for t in _failed_attempts.get(client_ip, []) if now - t < _FAILURE_WINDOW]
    if attempts:
        _failed_attempts[client_ip] = attempts
    if len(attempts) >= _MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many failed authentication attempts")


def _record_failed_attempt(request: Request) -> None:
    client_ip = _client_ip(request)
    _failed_attempts.setdefault(client_ip, []).append(_now())
    if len(_failed_attempts) > _MAX_TRACKED_IPS:
        _failed_attempts.clear()


async def _auth_failure_backoff() -> None:
    """Per-failure delay, isolated so tests can stub the sleep to no-op."""
    await asyncio.sleep(_AUTH_FAILURE_BACKOFF)


async def _verify_token(request: Request, env_var_name: str) -> None:
    _check_rate_limit(request)

    token = os.environ.get(env_var_name)
    if not token:
        raise HTTPException(status_code=503, detail=f"{env_var_name} not configured")

    if len(token) < 16:
        raise HTTPException(status_code=503, detail=f"{env_var_name} is too weak (min 16 chars)")

    presented = _extract_presented_token(request)
    if presented is None:
        _record_failed_attempt(request)
        raise HTTPException(status_code=401, detail="Missing bearer token")

    if not hmac.compare_digest(presented, token):
        _record_failed_attempt(request)
        await _auth_failure_backoff()
        raise HTTPException(status_code=403, detail="Invalid API token")


async def require_api_token(request: Request) -> None:
    await _verify_token(request, "API_AUTH_TOKEN")

async def require_manual_confirm_token(request: Request) -> None:
    await _verify_token(request, "PAYONEER_TX_TOKEN")

async def require_withdrawal_token(request: Request) -> None:
    await _verify_token(request, "WITHDRAWAL_TOKEN")


def _extract_presented_token(request: Request) -> str | None:
    header = request.headers.get(AUTH_HEADER, "")
    if header.startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX):]
    return request.cookies.get(SESSION_COOKIE)
