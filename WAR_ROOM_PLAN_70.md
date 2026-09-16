# Issue #70: APScheduler async wrapper fix

## Problem
`_trigger_research` and `_trigger_earning_cycle` use `asyncio.get_event_loop()` from BackgroundScheduler worker threads. On Python 3.11+ this raises RuntimeError in non-main threads. Both jobs silently no-op.

## Fix approach
Capture the running event loop once at startup and use it in the scheduler wrappers.

## Subtasks
- [x] Fix `SurvivalLoop.__init__` to reliably capture the event loop (store as `self._event_loop`)
- [x] Rewrite `_trigger_research` and `_trigger_earning_cycle` wrappers to use `self._event_loop` instead of `asyncio.get_event_loop()`
- [x] Add unit test proving the wrapper actually executes the coroutine on a real running loop
- [ ] Run full test suite and fix any failures
