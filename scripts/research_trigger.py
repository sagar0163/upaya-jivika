#!/usr/bin/env python3
"""Research trigger script for GitHub Actions cron job.

This script runs the research cycle and persists results to Supabase.
It can be called independently of the live web process.
"""

import asyncio
import sys

# Add project root to path
sys.path.insert(0, '.')

from src.persistence import create_persistence_store
from src.research_loop import ResearchAgent, persist_research_scores


async def main() -> int:
    """Run a single research cycle."""
    store = create_persistence_store()

    agent = ResearchAgent()

    try:
        print("Research cycle starting...")
        results = await agent.research_earning_platforms()

        # Persist platform certainties / task affinities so the TaskScorer's
        # select_from_research sees fresh research data after every 6 h run
        # (issue #60 closed loop).
        scores = persist_research_scores(results, store)
        print(f"Persisted {len(scores)} platform-certainty score(s) to DB")

        # Persist results to the shared event log so they are consistent
        # whether the cycle ran via APScheduler in the live app or via cron.
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