# Issue #72: Fail-closed vault — reject plaintext credential writes in production

## Goal
Never store plaintext credentials in the live Supabase vault. Fail closed when
`VAULT_ENCRYPTION_KEY` is unset in production instead of silently writing
cleartext. Provide a migration script to encrypt existing legacy plaintext rows.

## Checklist

- [x] Add `PlaintextVaultError` exception and `_is_production()` helper to vault.py
- [x] Fail-closed `set()`: raise when encryption key unset + production + no `ALLOW_PLAINTEXT_VAULT=1`
- [x] Add startup check: log ERROR (or abort) if live vault operating without key
- [x] Create migration script `scripts/migrate_plaintext_vault.py`
- [x] Update tests: fail-closed set, startup check, migration logic
- [x] Update render.yaml comment + README to reflect fail-closed behavior
- [x] Run full test suite, fix failures
- [ ] Remove plan file and final commit