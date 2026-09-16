# WAR ROOM PLAN — Issue #76: Human delegation with escrow (no-upfront-payment)

Core implementation was completed + committed in `f954b85` (delegation.py,
wallet escrow, task_executor fallback, audit trail, persistence, main.py wiring,
API endpoints). Remaining work below. Baseline: 791 tests pass.

## Subtask checklist

- [x] Create `src/delegation.py` (Commitment model + DelegationHub: escrow via ai_spend/approval_gate, release only on verification, refund on expiry, audit/cold/event/persist wiring) — done in f954b85
- [x] Add `escrow` pool + hold/release/refund to `src/wallet.py` — done in f954b85
- [x] Add delegation fallback to `task_executor.run_earning_cycle` (low-certainty + executor-failed paths) — done in f954b85
- [x] Add `record_delegation` to `src/audit_trail.py` — done in f954b85
- [x] Add commitment persistence (save/load + InMemoryStore) to `src/persistence.py` — done in f954b85
- [x] Wire DelegationHub + `/api/delegation/*` endpoints + status summary + survival-tick expiry in `main.py` — done in f954b85
- [x] Dashboard surfacing: wallet escrow + active-delegation summary in `static/index.html`
- [x] Unit tests `tests/test_delegation.py`: escrow funded via ai_spend/approval_gate, released only on verified delivery
- [x] Unit tests: no-upfront-payment invariant (release without verification raises ScamPreventionError)
- [x] Unit tests: deadline expiry → escrow refund to wallet; cancelled refund too
- [x] Unit tests: delegation transitions surfaced in audit trail, cold archive, events
- [x] Integration test: task_executor delegates on executor-failed + low-certainty path
- [x] Run full test suite; fix any failures
- [ ] (final) delete plan file, final commit, push branch