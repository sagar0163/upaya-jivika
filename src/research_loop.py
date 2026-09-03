"""Research Loop - Autonomous web research using DuckDuckGo + Jina AI + NVIDIA reasoning.

Research loop trigger: Every 6h · on survival state change · when task queue is empty
Research gate: Tasks score >0.85 certainty to enter queue (relaxed in Critical/Terminal states)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from duckduckgo_search import DDGS
from pydantic import BaseModel

from src.brain_router import (
    CompletionRequest,
    TaskType,
    get_brain_router,
)

logger = logging.getLogger(__name__)


class ResearchTopic(str, Enum):
    """Topics the agent can research."""
    EARNING_PLATFORMS = "earning_platforms"
    PLATFORM_TOS = "platform_tos"
    PAY_RATES = "pay_rates"
    TASK_AVAILABILITY = "task_availability"
    USER_REVIEWS = "user_reviews"
    PAYMENT_METHODS = "payment_methods"


class ResearchState(str, Enum):
    """State of a research task."""
    PENDING = "pending"
    SEARCHING = "searching"
    READING = "reading"
    REASONING = "reasoning"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ResearchResult:
    """Result of a research operation."""
    topic: ResearchTopic
    query: str
    findings: List[Dict[str, Any]]
    summary: str
    confidence: float
    sources: List[str]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    state: ResearchState = ResearchState.COMPLETED


class SearchResult(BaseModel):
    """Single search result from DuckDuckGo."""
    title: str
    href: str
    body: str


class ResearchQuery(BaseModel):
    """A research query to execute."""
    topic: ResearchTopic
    query: str
    max_results: int = 10
    priority: int = 1


class DDGSearcher:
    """DuckDuckGo searcher with rate limiting."""

    def __init__(self, max_rpm: int = 30):
        self.max_rpm = max_rpm
        self._request_count = 0
        self._last_reset = 0.0
        self.ddgs = DDGS()

    async def _check_rate_limit(self) -> bool:
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = asyncio.get_event_loop().time()
        if now - self._last_reset >= 60:
            self._request_count = 0
            self._last_reset = now
        return self._request_count < self.max_rpm

    async def _increment(self):
        self._request_count += 1

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """Search DuckDuckGo and return results."""
        if not await self._check_rate_limit():
            raise Exception("DDG rate limit exceeded")

        await self._increment()

        # Run in executor since DDGS is synchronous
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: list(self.ddgs.text(query, max_results=max_results))
        )

        return [
            SearchResult(
                title=r.get("title", ""),
                href=r.get("href", ""),
                body=r.get("body", "")
            )
            for r in results
        ]


class JinaReader:
    """Jina AI Reader - converts any URL to clean markdown."""

    def __init__(self):
        self.base_url = "https://r.jina.ai/http://"
        self.client = httpx.AsyncClient(timeout=30.0)
        self._request_count = 0
        self._last_reset = 0.0
        self.max_rpm = 200

    async def _check_rate_limit(self) -> bool:
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = asyncio.get_event_loop().time()
        if now - self._last_reset >= 60:
            self._request_count = 0
            self._last_reset = now
        return self._request_count < self.max_rpm

    async def _increment(self):
        self._request_count += 1

    async def read(self, url: str) -> Optional[str]:
        """Read a URL and return clean markdown."""
        if not await self._check_rate_limit():
            logger.warning("Jina AI rate limit exceeded")
            return None

        await self._increment()

        # Jina AI Reader format: https://r.jina.ai/http://<url>
        # For https URLs, we need to handle properly
        if url.startswith("https://"):
            jina_url = f"https://r.jina.ai/http://{url[8:]}"
        elif url.startswith("http://"):
            jina_url = f"https://r.jina.ai/http://{url[7:]}"
        else:
            jina_url = f"https://r.jina.ai/http://{url}"

        try:
            response = await self.client.get(jina_url)
            if response.status_code == 200:
                return response.text
            else:
                logger.warning(f"Jina AI failed for {url}: HTTP {response.status_code}")
                return None
        except Exception as e:
            logger.warning(f"Jina AI error for {url}: {e}")
            return None

    async def close(self):
        await self.client.aclose()


class ResearchAgent:
    """Autonomous research agent using DDG + Jina + NVIDIA reasoning."""

    def __init__(self):
        self.searcher = DDGSearcher()
        self.reader = JinaReader()
        self.router = get_brain_router()
        self._research_history: List[ResearchResult] = []

    async def research(self, query: ResearchQuery) -> ResearchResult:
        """Execute a full research cycle: search → read → reason."""
        logger.info(f"Starting research: {query.topic.value} - {query.query}")

        # Phase 1: Search
        search_results = await self.searcher.search(query.query, query.max_results)
        logger.info(f"Found {len(search_results)} search results")

        # Phase 2: Read top results
        findings = []
        sources = []

        for result in search_results[:5]:  # Read top 5
            content = await self.reader.read(result.href)
            if content:
                findings.append({
                    "title": result.title,
                    "url": result.href,
                    "snippet": result.body,
                    "content": content[:5000],  # Limit content size
                })
                sources.append(result.href)

        # Phase 3: Reason with NVIDIA NIM
        summary, confidence = await self._reason(query.topic, query.query, findings)

        result = ResearchResult(
            topic=query.topic,
            query=query.query,
            findings=findings,
            summary=summary,
            confidence=confidence,
            sources=sources,
            state=ResearchState.COMPLETED
        )

        self._research_history.append(result)
        return result

    async def _reason(
        self,
        topic: ResearchTopic,
        query: str,
        findings: List[Dict[str, Any]]
    ) -> tuple[str, float]:
        """Use NVIDIA NIM to reason about findings and produce summary."""

        if not findings:
            return "No relevant findings found.", 0.0

        # Prepare context for reasoning
        context_parts = []
        for i, f in enumerate(findings):
            context_parts.append(
                f"SOURCE {i+1}: {f['title']} ({f['url']})\n"
                f"SNIPPET: {f['snippet']}\n"
                f"CONTENT: {f['content'][:2000]}..."
            )

        context = "\n\n---\n\n".join(context_parts)

        system_prompt = """You are a research analyst for an autonomous AI agent that must earn real money to survive.
Your job is to synthesize web research findings into actionable intelligence.

Focus on:
1. Concrete facts: pay rates, payment methods, requirements, ToS restrictions
2. Actionability: Can the agent actually do this? What's needed?
3. Reliability: How trustworthy are the sources?
4. India-specific: Payment methods that work in India (Payoneer, UPI, etc.)

Return a JSON object with:
- summary: concise synthesis of findings (2-3 paragraphs)
- confidence: 0.0-1.0 score for how reliable/actionable this is
- key_facts: list of specific actionable facts
- warnings: any red flags or ToS concerns"""

        user_prompt = f"""Research Topic: {topic.value}
Query: {query}

FINDINGS:
{context}

Analyze and synthesize."""

        request = CompletionRequest(
            prompt=user_prompt,
            system_prompt=system_prompt,
            task_type=TaskType.COMPLEX,
            max_tokens=2048,
            temperature=0.3,
        )

        try:
            response = await self.router.complete(request)
            if response.success:
                # Try to parse JSON from response
                try:
                    parsed = json.loads(response.content)
                    summary = parsed.get("summary", response.content)
                    confidence = float(parsed.get("confidence", 0.5))
                    return summary, confidence
                except json.JSONDecodeError:
                    # Fallback: extract confidence from text
                    confidence_match = re.search(r'confidence["\s:]+([0-9.]+)', response.content, re.IGNORECASE)
                    confidence = float(confidence_match.group(1)) if confidence_match else 0.6
                    return response.content, confidence
            else:
                logger.error(f"Reasoning failed: {response.error}")
                return f"Reasoning failed: {response.error}", 0.0
        except Exception as e:
            logger.error(f"Reasoning exception: {e}")
            return f"Reasoning error: {str(e)}", 0.0

    async def research_earning_platforms(self) -> List[ResearchResult]:
        """Research current earning platforms and pay rates."""
        queries = [
            ResearchQuery(
                topic=ResearchTopic.EARNING_PLATFORMS,
                query="best microtask platforms 2024 Clickworker Toloka Prolific Appen pay rates Payoneer India",
                max_results=10,
            ),
            ResearchQuery(
                topic=ResearchTopic.PAY_RATES,
                query="Clickworker Toloka Prolific Appen current pay rates 2024 earnings per hour",
                max_results=10,
            ),
            ResearchQuery(
                topic=ResearchTopic.PLATFORM_TOS,
                query="Clickworker Toloka Prolific Appen terms of service automation bot policy 2024",
                max_results=10,
            ),
            ResearchQuery(
                topic=ResearchTopic.PAYMENT_METHODS,
                query="Payoneer India withdrawal 2024 fees RBI rules UPI bank transfer",
                max_results=10,
            ),
        ]

        results = []
        for query in queries:
            result = await self.research(query)
            results.append(result)
            await asyncio.sleep(2)  # Be nice to APIs

        return results

    async def research_specific_platform(self, platform: str) -> ResearchResult:
        """Deep research on a specific platform."""
        query = ResearchQuery(
            topic=ResearchTopic.EARNING_PLATFORMS,
            query=f"{platform} review 2024 pay rate payment method India automation ToS bot detection",
            max_results=15,
        )
        return await self.research(query)

    def get_history(self) -> List[ResearchResult]:
        """Get research history."""
        return self._research_history

    async def close(self):
        await self.reader.close()


class ResearchLoop:
    """Main research loop scheduler."""

    def __init__(self, agent: Optional[ResearchAgent] = None):
        self.agent = agent or ResearchAgent()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._interval_hours = 6
        self._last_run: Optional[datetime] = None
        self._trigger_event = asyncio.Event()

    async def start(self):
        """Start the research loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Research loop started")

    async def stop(self):
        """Stop the research loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.agent.close()
        logger.info("Research loop stopped")

    def trigger(self):
        """Trigger an immediate research run."""
        self._trigger_event.set()

    async def _run_loop(self):
        """Main loop: run every 6 hours or on trigger."""
        while self._running:
            try:
                # Wait for interval or trigger
                wait_task = asyncio.create_task(
                    asyncio.sleep(self._interval_hours * 3600)
                )
                trigger_task = asyncio.create_task(self._trigger_event.wait())

                done, pending = await asyncio.wait(
                    [wait_task, trigger_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                for task in pending:
                    task.cancel()

                self._trigger_event.clear()

                if not self._running:
                    break

                # Run research
                await self._run_research_cycle()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Research loop error: {e}")
                await asyncio.sleep(60)  # Back off on error

    async def _run_research_cycle(self):
        """Execute one research cycle."""
        logger.info("Starting research cycle")
        self._last_run = datetime.utcnow()

        try:
            # Research earning platforms
            results = await self.agent.research_earning_platforms()

            # Log results
            for result in results:
                logger.info(
                    f"Research: {result.topic.value} | "
                    f"Confidence: {result.confidence:.2f} | "
                    f"Sources: {len(result.sources)}"
                )

            # In a real system, this would feed into task_scorer.py
            # For now, just log
            logger.info(f"Research cycle complete. {len(results)} topics researched.")

        except Exception as e:
            logger.error(f"Research cycle failed: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get loop status."""
        return {
            "running": self._running,
            "interval_hours": self._interval_hours,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "history_count": len(self.agent.get_history()),
        }


# Convenience functions
async def quick_research(query: str, topic: ResearchTopic = ResearchTopic.EARNING_PLATFORMS) -> ResearchResult:
    """Quick one-off research."""
    agent = ResearchAgent()
    try:
        result = await agent.research(ResearchQuery(topic=topic, query=query))
        return result
    finally:
        await agent.close()


# Pre-defined research queries for survival agent
SURVIVAL_RESEARCH_QUERIES = [
    ResearchQuery(
        topic=ResearchTopic.EARNING_PLATFORMS,
        query="Clickworker India 2024 sign up pay rate Payoneer tasks available",
        max_results=10,
    ),
    ResearchQuery(
        topic=ResearchTopic.EARNING_PLATFORMS,
        query="Toloka Yandex India 2024 earnings Payoneer withdrawal minimum",
        max_results=10,
    ),
    ResearchQuery(
        topic=ResearchTopic.EARNING_PLATFORMS,
        query="Prolific Academic India 2024 eligibility pay rate PayPal studies",
        max_results=10,
    ),
    ResearchQuery(
        topic=ResearchTopic.EARNING_PLATFORMS,
        query="Appen data annotation India 2024 projects pay rate Payoneer",
        max_results=10,
    ),
    ResearchQuery(
        topic=ResearchTopic.PAYMENT_METHODS,
        query="Payoneer India 2024 fees withdrawal RBI UPI bank account KYC",
        max_results=10,
    ),
    ResearchQuery(
        topic=ResearchTopic.PLATFORM_TOS,
        query="microtask platform terms of service automation bot allowed prohibited 2024",
        max_results=10,
    ),
]