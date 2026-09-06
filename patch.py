import re
with open('src/task_executor.py', 'r') as f:
    content = f.read()

replacement = """        # 1. Check research data for highest-certainty task
        scorer = TaskScorer()
        research_candidate = scorer.select_from_research(current_debt)
        
        research_result = None
        if research_candidate:
            logger.info(f"Selected task from research: {research_candidate.title} on {research_candidate.platform.value}")
            # Try to find matching real tasks on that platform
            platform_candidates = await self.discover_tasks([research_candidate.platform])
            
            # Find best match based on task_type or just pick the best scored one
            if platform_candidates:
                scored = scorer.filter_executable(platform_candidates, current_debt)
                if scored:
                    best_real_candidate = scored[0].candidate
                    logger.info(f"Executing real task matching research: {best_real_candidate.title}")
                    research_result = await self.execute_task(best_real_candidate, certainty=min_certainty)
                    
                    # Feed outcome back to research data (simulate by updating DB or logging)
                    # For MVP: Save to persistence as a feedback loop
                    from src.persistence import create_persistence_store
                    store = create_persistence_store()
                    # We can store this as a new research score or just log it for the prompt
                    feedback = f"Outcome for {research_candidate.platform.value}: Success={research_result.success}, Earned=${research_result.amount_earned}"
                    logger.info(feedback)
                    # The prompt says: "Learning carries across reincarnations via ancestral memory (initial version: save top 3 certainties + task affinities per life)"

        # If no research candidate or it failed, fall back to normal cycle
        # Or maybe we just return the research result if it succeeded
        results = []
        if research_result:
            results.append(research_result)
            
        # Continue with normal discovery for other platforms
        candidates = await self.discover_tasks(platforms)"""

# I need to know exactly what's expected for feeding back to research data.
