# WAR_ROOM_PLAN_71.md — Issue #71: Dedupe + Single-Source Schedules, Batch Persistence, Bound Logs

## Problem Summary
- Research fires from BOTH in-app BackgroundScheduler AND GH Actions cron with NO dedup
- `scripts/research_trigger.py` duplicates event-log append logic from `main.SurvivalLoop.research_trigger`
- `_persist_all()` fires 4 separate Supabase round-trips on every event
- `_event_log` and `AuditTrail` are unbounded Python lists

## Subtasks

- [ ] 1. Add `last_research_at` persistence + dedup guard to PersistenceStore (abstract + InMemory + Supabase)
- [ ] 2. Add dedup guard to `scripts/research_trigger.py` (check `last_research_at` before firing)
- [ ] 3. Add dedup guard to `main.py` `research_trigger` + `_trigger_research` wrapper
- [ ] 4. Add dedup guard to `/api/research/trigger` endpoint
- [ ] 5. Remove duplicated event-log append from `scripts/research_trigger.py` (event-log append stays in main only)
- [ ] 6. Add dirty flags + `persist_dirty()` to SurvivalLoop (only save changed entities)
- [ ] 7. Bound `_event_log` with max size + cold archive overflow
- [ ] 8. Bound `AuditTrail` with max size + flush to cold archive
- [ ] 9. `survival_tick` persists on state change only, not wall clock
- [ ] 10. Write tests for new dedup, batch persistence, and bounded-log behavior
- [ ] 11. Run tests, fix failures
- [ ] 12. Commit, push
