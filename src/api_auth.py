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

import hmac
import os

from fastapi import HTTPException, Request

AUTH_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "

#: Name of the httpOnly session cookie set by ``POST /api/session``. Using a
#: cookie the page's own JS can never read (rather than ``localStorage``)
#: means a future XSS on the dashboard can't steal the token by reading
#: browser storage — the cookie is invisible to page script either way.
SESSION_COOKIE = "uj_session"


def require_api_token(request: Request) -> None:
    """FastAPI dependency: reject the request unless it carries a valid token.

    Raises 503 if ``API_AUTH_TOKEN`` isn't configured (fail closed — a
    forgotten secret must not silently open every write endpoint), 401 if
    the caller didn't present a token, and 403 if it doesn't match. Accepts
    the token either as an ``Authorization: Bearer`` header (for direct API
    callers) or as the ``uj_session`` cookie set by ``POST /api/session``
    (used by the dashboard, which never handles the raw token in JS after
    the initial login prompt).
    """
    token = os.environ.get("API_AUTH_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="API authentication not configured")

    presented = _extract_presented_token(request)
    if presented is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=403, detail="Invalid API token")


def _extract_presented_token(request: Request) -> str | None:
    header = request.headers.get(AUTH_HEADER, "")
    if header.startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX):]
    return request.cookies.get(SESSION_COOKIE)
