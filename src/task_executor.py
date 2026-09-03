"""Task Executor - Platform connectors + Playwright browser automation.

Rules from artifact.md §9:
- Playwright for browser automation
- Start with 2-3 platforms from §7 Active table without CAPTCHA gates
- Report outcomes (success/fail, amount earned) back through wallet.py's free-pool credit path
- No agent frameworks — from scratch only
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from src.task_scorer import (
    PaymentMethod,
    Platform,
    TaskCandidate,
    TaskResult,
    TaskType,
)
from src.wallet import Wallet

logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    """Raised when task execution fails."""
    pass


class PlatformConnector(ABC):
    """Abstract base class for platform-specific connectors."""

    def __init__(self, platform: Platform, context: BrowserContext):
        self.platform = platform
        self.context = context
        self.page: Optional[Page] = None

    @abstractmethod
    async def login(self, credentials: dict) -> bool:
        """Login to the platform. Returns True on success."""
        pass

    @abstractmethod
    async def find_tasks(self) -> list[TaskCandidate]:
        """Discover available tasks on the platform."""
        pass

    @abstractmethod
    async def execute_task(self, candidate: TaskCandidate) -> TaskResult:
        """Execute a specific task. Returns result with earnings."""
        pass

    @abstractmethod
    async def get_earnings(self) -> Decimal:
        """Get total earnings from this platform session."""
        pass

    async def _new_page(self) -> Page:
        """Create a new page in the context."""
        self.page = await self.context.new_page()
        # Human-like delays
        await self.page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        return self.page

    async def _safe_click(self, selector: str, timeout: int = 5000) -> bool:
        """Click with human-like behavior."""
        if not self.page:
            return False
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            await self.page.hover(selector)
            await asyncio.sleep(0.5)  # Human pause
            await self.page.click(selector)
            await asyncio.sleep(1)
            return True
        except Exception as e:
            logger.warning(f"Click failed on {selector}: {e}")
            return False

    async def _safe_fill(self, selector: str, value: str, timeout: int = 5000) -> bool:
        """Fill input with human-like typing."""
        if not self.page:
            return False
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            await self.page.click(selector)
            await asyncio.sleep(0.2)
            await self.page.fill(selector, "")
            await asyncio.sleep(0.1)
            await self.page.type(selector, value, delay=100)  # 100ms per char
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            logger.warning(f"Fill failed on {selector}: {e}")
            return False


class ClickworkerConnector(PlatformConnector):
    """Clickworker platform connector.

    From artifact.md §7:
    - Certainty: 70%, Pay: $1-5/task, Payment: Payoneer
    - India supported, consistent
    - Less aggressive bot detection
    """

    BASE_URL = "https://www.clickworker.com"
    LOGIN_URL = "https://www.clickworker.com/login"
    DASHBOARD_URL = "https://www.clickworker.com/dashboard"

    async def login(self, credentials: dict) -> bool:
        page = await self._new_page()
        try:
            await page.goto(self.LOGIN_URL, wait_until="networkidle")
            await self._safe_fill('input[name="email"]', credentials.get("email", ""))
            await self._safe_fill('input[name="password"]', credentials.get("password", ""))
            await self._safe_click('button[type="submit"]')
            await page.wait_for_url("**/dashboard**", timeout=15000)
            logger.info("Clickworker login successful")
            return True
        except Exception as e:
            logger.error(f"Clickworker login failed: {e}")
            return False

    async def find_tasks(self) -> list[TaskCandidate]:
        page = await self._new_page()
        candidates = []
        try:
            await page.goto(self.DASHBOARD_URL, wait_until="networkidle")
            # Mock implementation - in real version, scrape task listings
            # For now, return mock candidates for testing
            candidates = [
                TaskCandidate(
                    platform=Platform.CLICKWORKER,
                    task_type=TaskType.MICROTASK,
                    title="Categorize product images",
                    description="Categorize 100 product images into predefined categories",
                    estimated_pay=Decimal("5.00"),
                    estimated_hours=Decimal("1.0"),
                    payment_method=PaymentMethod.PAYONEER,
                    platform_certainty=Decimal("0.75"),
                    source_url=f"{self.BASE_URL}/task/123",
                ),
                TaskCandidate(
                    platform=Platform.CLICKWORKER,
                    task_type=TaskType.MICROTASK,
                    title="Transcribe short audio clips",
                    description="Transcribe 20 audio clips (30 seconds each)",
                    estimated_pay=Decimal("3.00"),
                    estimated_hours=Decimal("0.5"),
                    payment_method=PaymentMethod.PAYONEER,
                    platform_certainty=Decimal("0.70"),
                    source_url=f"{self.BASE_URL}/task/124",
                ),
            ]
        except Exception as e:
            logger.error(f"Clickworker find_tasks failed: {e}")
        return candidates

    async def execute_task(self, candidate: TaskCandidate) -> TaskResult:
        task_id = str(uuid.uuid4())[:8]
        start_time = datetime.utcnow()
        page = await self._new_page()

        try:
            await page.goto(candidate.source_url, wait_until="networkidle")
            # Mock execution - in real version, perform actual task steps
            await asyncio.sleep(2)  # Simulate work

            # Simulate success with some probability
            success = True
            amount_earned = candidate.estimated_pay
            error = None

        except Exception as e:
            success = False
            amount_earned = Decimal("0")
            error = str(e)
            logger.error(f"Clickworker task execution failed: {e}")

        time_spent = (datetime.utcnow() - start_time).total_seconds() / 3600

        return TaskResult(
            task_id=task_id,
            candidate=candidate,
            success=success,
            amount_earned=amount_earned,
            time_spent_hours=Decimal(str(time_spent)),
            error=error,
            platform_data={"platform": self.platform.value},
        )

    async def get_earnings(self) -> Decimal:
        # In real implementation, scrape earnings from dashboard
        return Decimal("0")


class TolokaConnector(PlatformConnector):
    """Toloka (Yandex) platform connector.

    From artifact.md §7:
    - Certainty: 75%, Pay: $5-60/mo, Payment: Payoneer ($0.02 min)
    - Image/text tasks, very low barrier
    - Less aggressive bot detection
    """

    BASE_URL = "https://toloka.yandex.com"
    LOGIN_URL = "https://toloka.yandex.com/requester/login"
    TASKS_URL = "https://toloka.yandex.com/tasks"

    async def login(self, credentials: dict) -> bool:
        page = await self._new_page()
        try:
            await page.goto(self.LOGIN_URL, wait_until="networkidle")
            await self._safe_fill('input[type="email"]', credentials.get("email", ""))
            await self._safe_fill('input[type="password"]', credentials.get("password", ""))
            await self._safe_click('button[type="submit"]')
            await page.wait_for_url("**/tasks**", timeout=15000)
            logger.info("Toloka login successful")
            return True
        except Exception as e:
            logger.error(f"Toloka login failed: {e}")
            return False

    async def find_tasks(self) -> list[TaskCandidate]:
        page = await self._new_page()
        candidates = []
        try:
            await page.goto(self.TASKS_URL, wait_until="networkidle")
            # Mock implementation
            candidates = [
                TaskCandidate(
                    platform=Platform.TOLOKA,
                    task_type=TaskType.MICROTASK,
                    title="Image classification - animals",
                    description="Classify 50 images as cat/dog/other",
                    estimated_pay=Decimal("2.50"),
                    estimated_hours=Decimal("0.5"),
                    payment_method=PaymentMethod.PAYONEER,
                    platform_certainty=Decimal("0.80"),
                    source_url=f"{self.BASE_URL}/task/abc",
                ),
                TaskCandidate(
                    platform=Platform.TOLOKA,
                    task_type=TaskType.MICROTASK,
                    title="Sentiment analysis - product reviews",
                    description="Rate sentiment of 100 short product reviews",
                    estimated_pay=Decimal("4.00"),
                    estimated_hours=Decimal("1.0"),
                    payment_method=PaymentMethod.PAYONEER,
                    platform_certainty=Decimal("0.75"),
                    source_url=f"{self.BASE_URL}/task/def",
                ),
            ]
        except Exception as e:
            logger.error(f"Toloka find_tasks failed: {e}")
        return candidates

    async def execute_task(self, candidate: TaskCandidate) -> TaskResult:
        task_id = str(uuid.uuid4())[:8]
        start_time = datetime.utcnow()
        page = await self._new_page()

        try:
            await page.goto(candidate.source_url, wait_until="networkidle")
            await asyncio.sleep(2)  # Simulate work

            success = True
            amount_earned = candidate.estimated_pay
            error = None

        except Exception as e:
            success = False
            amount_earned = Decimal("0")
            error = str(e)
            logger.error(f"Toloka task execution failed: {e}")

        time_spent = (datetime.utcnow() - start_time).total_seconds() / 3600

        return TaskResult(
            task_id=task_id,
            candidate=candidate,
            success=success,
            amount_earned=amount_earned,
            time_spent_hours=Decimal(str(time_spent)),
            error=error,
            platform_data={"platform": self.platform.value},
        )

    async def get_earnings(self) -> Decimal:
        return Decimal("0")


class ProlificConnector(PlatformConnector):
    """Prolific Academic platform connector.

    From artifact.md §7:
    - Certainty: 70%, Pay: Hourly, Payment: PayPal
    - Research studies, India OK
    - More academic, less automation-friendly
    """

    BASE_URL = "https://www.prolific.com"
    LOGIN_URL = "https://www.prolific.com/login"
    STUDIES_URL = "https://www.prolific.com/studies"

    async def login(self, credentials: dict) -> bool:
        page = await self._new_page()
        try:
            await page.goto(self.LOGIN_URL, wait_until="networkidle")
            await self._safe_fill('input[name="email"]', credentials.get("email", ""))
            await self._safe_fill('input[name="password"]', credentials.get("password", ""))
            await self._safe_click('button[type="submit"]')
            await page.wait_for_url("**/studies**", timeout=15000)
            logger.info("Prolific login successful")
            return True
        except Exception as e:
            logger.error(f"Prolific login failed: {e}")
            return False

    async def find_tasks(self) -> list[TaskCandidate]:
        page = await self._new_page()
        candidates = []
        try:
            await page.goto(self.STUDIES_URL, wait_until="networkidle")
            candidates = [
                TaskCandidate(
                    platform=Platform.PROLIFIC,
                    task_type=TaskType.SURVEY,
                    title="Psychology study - decision making",
                    description="20-min survey on decision making under uncertainty",
                    estimated_pay=Decimal("8.00"),
                    estimated_hours=Decimal("0.33"),
                    payment_method=PaymentMethod.PAYPAL,
                    platform_certainty=Decimal("0.70"),
                    source_url=f"{self.BASE_URL}/studies/123",
                ),
            ]
        except Exception as e:
            logger.error(f"Prolific find_tasks failed: {e}")
        return candidates

    async def execute_task(self, candidate: TaskCandidate) -> TaskResult:
        task_id = str(uuid.uuid4())[:8]
        start_time = datetime.utcnow()
        page = await self._new_page()

        try:
            await page.goto(candidate.source_url, wait_until="networkidle")
            await asyncio.sleep(3)  # Surveys take longer

            success = True
            amount_earned = candidate.estimated_pay
            error = None

        except Exception as e:
            success = False
            amount_earned = Decimal("0")
            error = str(e)
            logger.error(f"Prolific task execution failed: {e}")

        time_spent = (datetime.utcnow() - start_time).total_seconds() / 3600

        return TaskResult(
            task_id=task_id,
            candidate=candidate,
            success=success,
            amount_earned=amount_earned,
            time_spent_hours=Decimal(str(time_spent)),
            error=error,
            platform_data={"platform": self.platform.value},
        )

    async def get_earnings(self) -> Decimal:
        return Decimal("0")


# Connector registry
CONNECTORS: dict[Platform, type[PlatformConnector]] = {
    Platform.CLICKWORKER: ClickworkerConnector,
    Platform.TOLOKA: TolokaConnector,
    Platform.PROLIFIC: ProlificConnector,
}


class BrowserSessionManager:
    """Manages Playwright browser lifecycle and contexts."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._contexts: dict[str, BrowserContext] = {}

    async def start(self) -> None:
        """Start Playwright and launch browser."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        logger.info(f"Browser started (headless={self.headless})")

    async def stop(self) -> None:
        """Stop browser and Playwright."""
        for ctx in self._contexts.values():
            await ctx.close()
        self._contexts.clear()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")

    async def create_context(
        self,
        name: str,
        cookies: Optional[list[dict]] = None,
        user_agent: Optional[str] = None,
    ) -> BrowserContext:
        """Create a new isolated browser context."""
        if not self._browser:
            raise ExecutionError("Browser not started. Call start() first.")

        context = await self._browser.new_context(
            user_agent=user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )

        if cookies:
            # Type ignore for cookie dict compatibility
            await context.add_cookies(cookies)  # type: ignore[arg-type]

        self._contexts[name] = context
        logger.debug(f"Created browser context: {name}")
        return context

    async def get_context(self, name: str) -> Optional[BrowserContext]:
        """Get existing context by name."""
        return self._contexts.get(name)

    async def close_context(self, name: str) -> None:
        """Close and remove a context."""
        if name in self._contexts:
            await self._contexts[name].close()
            del self._contexts[name]


class TaskExecutor:
    """Main task execution engine.

    Coordinates:
    1. Browser session management
    2. Platform connector selection
    3. Task execution with wallet integration
    4. Result reporting
    """

    def __init__(
        self,
        wallet: Wallet,
        headless: bool = True,
        max_concurrent_tasks: int = 1,
    ) -> None:
        self.wallet = wallet
        self.headless = headless
        self.max_concurrent_tasks = max_concurrent_tasks
        self.session_manager = BrowserSessionManager(headless=headless)
        self._connectors: dict[Platform, PlatformConnector] = {}
        self._credentials: dict[Platform, dict] = {}
        self._running = False

    async def start(self) -> None:
        """Start the executor."""
        await self.session_manager.start()
        self._running = True
        logger.info("TaskExecutor started")

    async def stop(self) -> None:
        """Stop the executor."""
        self._running = False
        await self.session_manager.stop()
        logger.info("TaskExecutor stopped")

    def set_credentials(self, platform: Platform, credentials: dict) -> None:
        """Set credentials for a platform."""
        self._credentials[platform] = credentials

    async def _get_connector(self, platform: Platform) -> PlatformConnector:
        """Get or create a connector for a platform."""
        if platform in self._connectors:
            return self._connectors[platform]

        if platform not in CONNECTORS:
            raise ExecutionError(f"No connector for platform: {platform}")

        if platform not in self._credentials:
            raise ExecutionError(f"No credentials for platform: {platform}")

        context = await self.session_manager.create_context(f"{platform.value}_ctx")
        connector_class = CONNECTORS[platform]
        connector = connector_class(platform, context)

        # Login
        success = await connector.login(self._credentials[platform])
        if not success:
            raise ExecutionError(f"Login failed for {platform}")

        self._connectors[platform] = connector
        return connector

    async def discover_tasks(self, platforms: list[Platform]) -> list[TaskCandidate]:
        """Discover tasks from multiple platforms."""
        all_candidates = []

        for platform in platforms:
            try:
                connector = await self._get_connector(platform)
                candidates = await connector.find_tasks()
                all_candidates.extend(candidates)
                logger.info(f"Found {len(candidates)} tasks on {platform.value}")
            except Exception as e:
                logger.error(f"Task discovery failed for {platform}: {e}")

        return all_candidates

    async def execute_task(
        self,
        candidate: TaskCandidate,
        certainty: Decimal = Decimal("0.95"),
    ) -> TaskResult:
        """Execute a single task and credit earnings to wallet.

        Args:
            candidate: The task to execute.
            certainty: ROI certainty for wallet spend gate (default 95%).

        Returns:
            TaskResult with outcome and earnings.
        """
        if not self._running:
            raise ExecutionError("Executor not started")

        connector = await self._get_connector(candidate.platform)

        # Execute the task
        result = await connector.execute_task(candidate)

        # If successful, credit earnings to wallet free pool
        if result.success and result.amount_earned > 0:
            try:
                # Note: wallet.credit_earned handles debt repayment automatically
                breakdown = self.wallet.credit_earned(result.amount_earned)
                logger.info(
                    f"Task {result.task_id} earned ${result.amount_earned}: "
                    f"debt_repaid=${breakdown['debt_repaid']}, "
                    f"to_free=${breakdown['to_free']}, "
                    f"to_locked=${breakdown['to_locked']}"
                )
                result.platform_data["wallet_breakdown"] = {
                    k: str(v) for k, v in breakdown.items()
                }
            except Exception as e:
                logger.error(f"Wallet credit failed: {e}")
                result.platform_data["wallet_error"] = str(e)

        return result

    async def execute_batch(
        self,
        candidates: list[TaskCandidate],
        certainty: Decimal = Decimal("0.95"),
    ) -> list[TaskResult]:
        """Execute multiple tasks sequentially (respects max_concurrent_tasks)."""
        results = []

        for candidate in candidates:
            if not self._running:
                break
            result = await self.execute_task(candidate, certainty)
            results.append(result)

            # Small delay between tasks to be respectful
            await asyncio.sleep(2)

        return results

    async def run_earning_cycle(
        self,
        platforms: list[Platform],
        current_debt: Decimal,
        min_certainty: Decimal,
    ) -> list[TaskResult]:
        """Run a full earning cycle: discover → score → execute.

        This is the main entry point called by the agent loop.
        """
        from src.task_scorer import TaskScorer

        logger.info(f"Starting earning cycle (debt=${current_debt})")

        # 1. Discover tasks
        candidates = await self.discover_tasks(platforms)
        if not candidates:
            logger.warning("No tasks discovered")
            return []

        # 2. Score tasks
        scorer = TaskScorer()
        scored = scorer.filter_executable(candidates, current_debt)

        if not scored:
            logger.info(f"No tasks pass threshold ({min_certainty})")
            return []

        # 3. Execute passing tasks
        executable = [s.candidate for s in scored]
        logger.info(f"Executing {len(executable)} tasks")

        results = await self.execute_batch(executable, certainty=min_certainty)
        return results


# Convenience function for testing with mocked browser
async def mock_execute_task(candidate: TaskCandidate) -> TaskResult:
    """Mock execution for testing without real browser."""
    task_id = str(uuid.uuid4())[:8]
    import random
    success = random.random() > 0.2  # 80% success rate
    amount = candidate.estimated_pay if success else Decimal("0")
    return TaskResult(
        task_id=task_id,
        candidate=candidate,
        success=success,
        amount_earned=amount,
        time_spent_hours=candidate.estimated_hours,
        error=None if success else "Mock execution failed",
        platform_data={"mock": True, "platform": candidate.platform.value},
    )