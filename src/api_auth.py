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


def require_api_token(request: Request) -> None:
    """FastAPI dependency: reject the request unless it carries a valid token.

    Raises 503 if ``API_AUTH_TOKEN`` isn't configured (fail closed — a
    forgotten secret must not silently open every write endpoint), 401 if
    the caller didn't present a token, and 403 if it doesn't match.
    """
    token = os.environ.get("API_AUTH_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="API authentication not configured")

    header = request.headers.get(AUTH_HEADER, "")
    if not header.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    presented = header[len(_BEARER_PREFIX):]
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=403, detail="Invalid API token")
