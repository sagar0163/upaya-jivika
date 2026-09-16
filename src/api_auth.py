"""Bearer-token guard for the HTTP and WebSocket surface.

The dashboard is intentionally reachable (its HTML shell is the login prompt),
but every endpoint that returns the agent's real state — wallet, debt, life,
survival state, earnings, pending AI-spend reasons, configured infrastructure,
and the live WebSocket feed — is gated (issue #74). This mirrors the
fail-closed pattern already used for the Payoneer webhook: if
``API_AUTH_TOKEN`` isn't configured, every protected request is rejected
rather than silently accepted.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

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
    forgotten secret must not silently open every protected endpoint), 401 if
    the caller didn't present a token, and 403 if it doesn't match. Accepts
    the token either as an ``Authorization: Bearer`` header (for direct API
    callers) or as the ``uj_session`` cookie set by ``POST /api/session``
    (used by the dashboard, which never handles the raw token in JS after
    the initial login prompt).
    """
    token = os.environ.get("API_AUTH_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="API authentication not configured")

    presented = extract_presented_token(request)
    if presented is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=403, detail="Invalid API token")


def extract_presented_token(carrier: Any) -> str | None:
    """Return the token presented on a request or WebSocket handshake.

    ``carrier`` can be a starlette ``Request`` or`` WebSocket`` — both expose
    ``.headers``, ``.cookies`` and ``.query_params``. The token is accepted
    from (in priority order):

    - the ``Authorization: Bearer <token>`` header (HTTP API callers and
      WebSocket clients that can set handshake headers)
    - the ``uj_session`` httpOnly cookie (the dashboard — browsers send
      cookies automatically on the same-origin WebSocket handshake)
    - the ``?token=<token>`` query param (WebSocket clients that can't set
      headers, e.g. the ``websockets`` library)
    """
    header = carrier.headers.get(AUTH_HEADER, "")
    if header.startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX):]
    cookie = carrier.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    return carrier.query_params.get("token")
