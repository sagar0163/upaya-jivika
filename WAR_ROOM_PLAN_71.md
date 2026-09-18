# Issue #71: Dedupe + single-source schedules, batch persistence, bounded logs

## Prior work status (from existing commits on this branch)

- [x] Add `last_research_at` persistence (`src/persistence.py`)
- [x] Add dedup guard to `scripts/research_trigger.py`
- [x] Add dedup guard to `main.py` `SurvivalLoop.research_trigger`
- [x] Dirty-flag batch persistence in `_persist_all`, `survival_tick` persists on state change
- [x] Bound `_event_log` and `AuditTrail` with cold-archive rollover
- [x] Atomic 6h research-window claim (shared `research_windows` table)
- [x] Tests for dedup guards, bounded logs, batch persistence

## Remaining work (found during review of prior commits)

- [x] Fix regression in `scripts/research_trigger.py`: `events` is undefined (NameError) and `save_last_research_at` was removed — restore both so the standalone cron path persists events and records the dedup timestamp
- [x] Add regression test covering the script's event-log append + dedup timestamp write
- [ ] Run full test suite + ruff; fix any failures
- [ ] Remove plan file, final commit referencing #71, push branch