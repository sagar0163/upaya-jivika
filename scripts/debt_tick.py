#!/usr/bin/env python3
"""Debt tick script for GitHub Actions cron job.

This script fires a single debt tick and persists the updated state to Supabase.
It can be called independently of the live web process.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Add project root to path
sys.path.insert(0, '.')

from src.persistence import create_persistence_store
from src.debt_engine import DebtEngine, DebtState, DifficultyMode


async def main() -> int:
    """Run a single debt tick."""
    store = create_persistence_store()

    # Load current debt state
    debt_state = store.load_debt_state()
    if debt_state is None:
        print("No debt state found — initializing life 1")
        debt_state = DebtState(
            debt=Decimal("0.00"),
            mode=DifficultyMode.NORMAL,
            alive=True,
            life_number=1,
            born_at=datetime.now(timezone.utc),
        )

    engine = DebtEngine(mode=debt_state.mode)
    engine.restore(debt_state)

    # Deduplicate against the in-process APScheduler job.
    # Both the cron workflow and the live app fire debt ticks; only one may
    # charge per ~24h window to avoid double-charging existence debt.
    if debt_state is not None and debt_state.last_tick_at is not None:
        now = datetime.now(timezone.utc)
        if (now - debt_state.last_tick_at) < timedelta(hours=23):
            print(
                f"Skipping — debt tick already ran at "
                f"{debt_state.last_tick_at.isoformat()} (debt=${debt_state.debt})"
            )
            return 0

    # Fire a single tick
    new_debt = engine.tick_now()

    # Save updated state
    store.save_debt_state(engine.snapshot())

    print(f"Debt tick completed: debt = ${new_debt}")
    if not engine.alive:
        print("DEATH TRIGGERED")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))