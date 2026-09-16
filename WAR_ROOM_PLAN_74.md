# WAR ROOM PLAN — Issue #74: Gate read/status/dashboard/WebSocket surface behind auth

## Goal
The following are currently exposed unauthenticated on the public URL:
`GET /` (dashboard HTML), `GET /status`, `GET /health` (returns debt/life/earnings),
`GET /ws` (WebSocket pushes wallet/debt/life snapshot), `GET /api/spend/pending`,
`GET /api/email/status`, `GET /api/survival-mode`.

Gate the read surface behind `require_api_token` (same token + httpOnly session
cookie the write endpoints already use), keep a bare-liveness `/health`, and add
WebSocket token auth.

## Approach decisions
- `/` stays public (it is the login + prompt surface; it carries no data — all
  data flows through the now-gated `/status`, `/ws`, `/api/spend/pending`).
  The dashboard JS will prompt for auth on 401/403 and on WS rejection.
- `/health` becomes a bare liveness probe: `{"status": "ok"}` — no
  debt/life/earnings/research-trigger fields (uptime monitors need nothing more).
- Sensitive read endpoints get `dependencies=[Depends(require_api_token)]`.
- WebSocket auth: token via query param `?token=`, `Authorization` header, or
  the `uj_session` cookie (browsers send cookies on the WS handshake). Reject
  with close codes 4401 (missing) / 4403 (invalid) BEFORE accepting.

## Subtasks
- [ ] 1. Gate `/status`, `/api/spend/pending`, `/api/email/status`,
      `/api/survival-mode` (GET) with `require_api_token`
- [ ] 2. Split `/health` into bare public liveness (drop debt/life/earnings/
      research-trigger fields)
- [ ] 3. Add WebSocket token auth (query param | Authorization header |
      `uj_session` cookie), reject with 4401/4403 before accept
- [ ] 4. Update dashboard JS: authedFetch for `/api/spend/pending`, prompt for
      auth on WS rejection (4401/4403) then reconnect, session-cookie-aware
      (no prompt when cookie already valid)
- [ ] 5. Update existing tests + add new tests for the gated surface
      (HTTP 401/403, public /health liveness, WS rejection + acceptance)
- [ ] 6. Update docs (src/api_auth.py docstring, README.md, artifact.md,
      render.yaml comments) to reflect the now-gated read surface
- [ ] 7. Run `ruff check src tests` + full `pytest`; fix failures
- [ ] 8. Final commit (removes this plan file) + push branch

## Verification
- `pytest` suite green (uses `API_AUTH_TOKEN=test-token` from conftest).
- `ruff check` (E, F, I selects) clean.