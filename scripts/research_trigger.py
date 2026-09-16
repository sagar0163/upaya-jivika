#!/usr/bin/env python3
"""Research trigger script for GitHub Actions cron job.

This script runs the research cycle and persists results to Supabase.
It can be called independently of the live web process.

Deduplication: the script checks ``last_research_at`` in persistence
before firing — if another runner (GH cron or in-app scheduler) already
completed a cycle within the last 6 hours, this invocation is skipped.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.insert(0, '.')

from src.persistence import create_persistence_store
from src.research_loop import ResearchAgent, persist_research_scores

# How long since the last research cycle before we allow another run.
DEDUP_WINDOW = timedelta(hours=6)


async def main() -> int:
    """Run a single research cycle."""
    store = create_persistence_store()

    # Deduplicate against the in-app APScheduler job (and other cron runs).
    # Only one runner may fire per 6 h window to avoid double-charging
    # free-tier API costs and racing on the event-log load-modify-write.
    last_at = store.load_last_research_at()
    if last_at is not None:
        now = datetime.now(timezone.utc)
        if (now - last_at) < DEDUP_WINDOW:
            print(
                f"Skipping — research cycle already ran at "
                f"{last_at.isoformat()} (within {DEDUP_WINDOW} dedup window)"
            )
            return 0

    agent = ResearchAgent()

    try:
        print("Research cycle starting...")
        results = await agent.research_earning_platforms()

        # Persist platform certainties / task affinities so the TaskScorer's
        # select_from_research sees fresh research data after every 6 h run
        # (issue #60 closed loop).
        scores = persist_research_scores(results, store)
        print(f"Persisted {len(scores)} platform-certainty score(s) to DB")

        # Record completion timestamp for dedup (shared with in-app scheduler).
        # The in-app scheduler reads this same field to avoid double-firing.
        store.save_last_research_at(datetime.now(timezone.utc))

        # Persist results to the shared event log.
        # NOTE: the event-log append for the LIVE app happens in
        # main.SurvivalLoop.research_trigger — this standalone script
        # only appends when called directly (GH Actions cron), since
        # there is no live SurvivalLoop instance to consume the results.
        events = store.load_events() or []
        for r in results:
            events.append(f"Research: {r.topic.value} (confidence {r.confidence:.2f})")
            print(f"Research: {r.topic.value} (confidence {r.confidence:.2f})")
        store.save_events(events)

        print(f"Research cycle complete: {len(results)} topics")
        return 0

    except Exception as e:
        print(f"Research cycle failed: {e}")
        return 1
    finally:
        await agent.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
