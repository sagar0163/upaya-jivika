# Issue #73 Plan
- [x] Split `API_AUTH_TOKEN` into `PAYONEER_TX_TOKEN` (for manual confirmation) and `WITHDRAWAL_TOKEN` (for withdrawals).
- [x] Update `main.py` and `src/api_auth.py` to use these new tokens for those endpoints, and enforce minimum length/strength for all tokens at boot.
- [x] Implement failed auth rate-limiting/throttling in `src/api_auth.py` (e.g., using `time.sleep` or throwing 429 if too many failed attempts occur).
- [x] Add freshness check to `verify_signature` in `src/payoneer_webhook.py` (fail replayed webhooks > 5 minutes old).
- [ ] Remove synchronous retry/sleep loop in the `payoneer_webhook` handler in `main.py` and return 5xx.
