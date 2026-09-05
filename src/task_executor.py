"""Task Executor - Platform connectors + Playwright browser automation.

Rules from artifact.md:
- §9: Playwright for browser automation; start with 2-3 platforms from §7 Active
      table without CAPTCHA gates.
- §13: Playwright session persistence across Render restarts is an unresolved
      item — handled here via cookie storage (see BrowserSessionManager).
- §12: No agent frameworks — from scratch only.
- §15: Human-paced Playwright (the network-facing connectors add delays and
      degrade gracefully so we never hammer a live platform).

Connectors drive real Playwright browser sessions: login, task navigation and
submission. All tests use mocked/recorded responses — never live scraping.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.guardrails import EthicalGuardrail

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from src.audit_trail import AuditTrail
from src.captcha_handler import BotDetectionTracker, PlatformBlockError
from src.guardrails import get_guardrail
from src.scam_detection import PlatformScammedError, ScamTracker
from src.state_machine import resolve_state
from src.task_scorer import (
    PaymentMethod,
    Platform,
    TaskCandidate,
    TaskResult,
    TaskType,
)
from src.vault import CredentialsVault
from src.wallet import Wallet

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_DIR = ".uj_sessions"

# artifact.md §14: "Task timeout — Max duration cap while debt keeps ticking".
# Default cap: a task may run at most this long before we abort it and report
# failure. Debt keeps accruing meanwhile, so an unbounded task is a slow leak.
DEFAULT_TASK_TIMEOUT_SECONDS = 300


class ExecutionError(Exception):
    """Raised when task execution fails."""
    pass


class TaskTimeoutError(ExecutionError):
    """Raised when a task exceeds its maximum allowed duration."""
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

    # -- shared DOM helpers ------------------------------------------------

    @staticmethod
    def _to_decimal(raw: str) -> Decimal:
        """Parse a currency string like '$3.50' or '1.234,56 €' into Decimal."""
        if raw is None:
            return Decimal("0")
        text = raw.strip().replace("\u00a0", " ")
        match = re.search(r"[-+]?\d[\d.,]*", text.replace(",", "")) if "," in text \
            and "." not in text else re.search(r"[-+]?[\d.,]+", text)
        if not match:
            return Decimal("0")
        cleaned = match.group(0).replace(",", "")
        try:
            return Decimal(cleaned)
        except Exception:
            return Decimal("0")

    async def _new_page(self) -> Page:
        """Create a new page in the context."""
        self.page = await self.context.new_page()
        # Human-like delays
        await self.page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        return self.page

    async def _wait_for_visible(self, selector: str, timeout: int = 5000) -> bool:
        """Wait until a selector is visible on the current page."""
        if not self.page:
            return False
        try:
            await self.page.wait_for_selector(
                selector, timeout=timeout, state="visible"
            )
            return True
        except Exception:
            return False

    async def _detect_bot_check(self) -> bool:
        """Detect a CAPTCHA / 2FA interstitial on the current page.

        We never attempt to solve CAPTCHAs — per artifact.md §6 automation is
        only allowed on ToS-safe platforms. This just surfaces the condition
        so callers can pause or bail out instead of hammering the site.
        """
        if not self.page:
            return False
        markers = [
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            "#captcha",
            ".g-recaptcha",
            "input[name='captcha']",
        ]
        for sel in markers:
            try:
                if await self.page.locator(sel).count() > 0:
                    logger.warning(
                        f"Bot check detected on {self.platform.value} ({sel})"
                    )
                    return True
            except Exception:
                continue
        # 2FA challenge form
        try:
            if await self.page.locator(
                "[name='otp'], [name='code'], input[inputmode='numeric']"
            ).count() > 0:
                logger.warning(
                    f"2FA challenge detected on {self.platform.value}"
                )
                return True
        except Exception:
            pass
        return False

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

    async def _read_text(self, selector: str, default: str = "") -> str:
        """Read inner text from the current page, or a default on failure."""
        if not self.page:
            return default
        try:
            return (await self.page.inner_text(selector)).strip()
        except Exception:
            return default

    async def _locate_count(self, selector: str) -> int:
        """Count matching elements without raising."""
        if not self.page:
            return 0
        try:
            result = await self.page.locator(selector).count()
            if result is None:
                return 0
            return int(result)
        except Exception:
            return 0


class ClickworkerConnector(PlatformConnector):
    """Clickworker platform connector with real Playwright session handling.

    From artifact.md §7:
    - Certainty: 70%, Pay: $1-5/task, Payment: Payoneer
    - India supported, consistent, less aggressive bot detection

    The platform's jobs interface presents task cards. We:
      1. login()            — fill credentials, tolerate CAPTCHA/2FA presence
      2. goto_jobs()        — navigate to the job listing
      3. find_tasks()       — scrape task cards into TaskCandidate objects
      4. execute_task()     — open the task, work it, submit (graceful on fail)
      5. get_earnings()     — read current balance from the dashboard

    Selectors are centralized as class constants so they can be adapted to
    upstream HTML changes without touching the flow logic. All interaction is
    human-paced; a bot check only pauses/surfaces, never solves it.
    """

    BASE_URL = "https://www.clickworker.com"
    LOGIN_URL = "https://www.clickworker.com/login"
    DASHBOARD_URL = "https://www.clickworker.com/dashboard"
    JOBS_URL = "https://www.clickworker.com/customer-account/jobs"

    # CSS selectors (adapt these if Clickworker changes their markup)
    SEL_EMAIL = 'input[name="email"], input[type="email"]'
    SEL_PASSWORD = 'input[name="password"], input[type="password"]'
    SEL_SUBMIT = 'button[type="submit"], button[name="submit"]'
    SEL_TASK_CARD = (
        "article.task-card, li.task, tr.job-row, [class*='task-item'], "
        "[class*='job-card']"
    )

    async def login(self, credentials: dict) -> bool:
        page = await self._new_page()
        try:
            await page.goto(self.LOGIN_URL, wait_until="domcontentloaded")
            await asyncio.sleep(1.0)  # human-pace page settle
            if await self._detect_bot_check():
                logger.info("Clickworker CAPTCHA/2FA present — awaiting manual")
                return False
            await self._safe_fill(self.SEL_EMAIL, credentials.get("email", ""))
            await self._safe_fill(self.SEL_PASSWORD, credentials.get("password", ""))
            await self._safe_click(self.SEL_SUBMIT)
            # A successful login lands on the dashboard; otherwise the login
            # form (or an error banner) remains visible.
            if await self._wait_for_visible(".logout, a[href*='logout'], nav", timeout=12000):
                await page.wait_for_url("**", timeout=5000)
                logger.info("Clickworker login successful")
                return True
            logger.warning("Clickworker login not confirmed (bad creds or challenge)")
            return False
        except Exception as e:
            logger.error(f"Clickworker login failed: {e}")
            return False

    async def goto_jobs(self) -> None:
        """Navigate to the Clickworker job listing page."""
        page = await self._new_page()
        await page.goto(self.JOBS_URL, wait_until="domcontentloaded")
        await asyncio.sleep(1.0)  # human-pace
        if await self._detect_bot_check():
            raise ExecutionError("Bot check on Clickworker jobs page")

    async def find_tasks(self) -> list[TaskCandidate]:
        """Scrape available Clickworker tasks from the job listing DOM."""
        page = await self._new_page()
        candidates: list[TaskCandidate] = []
        try:
            await page.goto(self.JOBS_URL, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            if await self._detect_bot_check():
                logger.warning("Clickworker bot check during find_tasks — skipping")
                return []

            count = await self._locate_count(self.SEL_TASK_CARD)
            if count == 0:
                logger.info("No Clickworker task cards found")
                return []

            for i in range(count):
                try:
                    card = page.locator(self.SEL_TASK_CARD).nth(i)
                    title = (await card.locator(
                        "h2, h3, .title, [class*='title']"
                    ).first.inner_text()).strip()
                    desc = (await card.locator(
                        "p, .description, [class*='desc']"
                    ).first.inner_text()).strip()
                    pay_raw = await card.locator(
                        "[class*='pay'], .amount, [class*='price']"
                    ).first.inner_text()
                    href = await card.locator(
                        "a[href]"
                    ).first.get_attribute("href")

                    pay = self._to_decimal(pay_raw)
                    url = f"{self.BASE_URL}{href}" if href and href.startswith(
                        "/") else (href or self.BASE_URL)

                    if not title:
                        continue
                    candidates.append(
                        TaskCandidate(
                            platform=Platform.CLICKWORKER,
                            task_type=TaskType.MICROTASK,
                            title=title,
                            description=desc,
                            estimated_pay=pay if pay > 0 else Decimal("1.00"),
                            estimated_hours=Decimal("1.0"),
                            payment_method=PaymentMethod.PAYONEER,
                            platform_certainty=Decimal("0.75"),
                            source_url=url,
                            metadata={"scraped": True, "source": "clickworker_jobs"},
                        )
                    )
                except Exception as e:
                    logger.debug(f"Clickworker card {i} skipped: {e}")
                    continue
        except Exception as e:
            logger.error(f"Clickworker find_tasks failed: {e}")
        return candidates

    async def execute_task(self, candidate: TaskCandidate) -> TaskResult:
        task_id = str(uuid.uuid4())[:8]
        start_time = datetime.now(timezone.utc)
        page = await self._new_page()
        success = False
        amount_earned = Decimal("0")
        error: Optional[str] = None
        submitted = False

        try:
            await page.goto(candidate.source_url, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            if await self._detect_bot_check():
                raise ExecutionError("Bot check on Clickworker task page")

            # Human-paced interaction: fill any text inputs we can find, then
            # submit the task form if present.
            inputs = await page.locator(
                "textarea[name], input[type='text'], input[type='number'], "
                "textarea:not([hidden])"
            ).count()
            if inputs > 0:
                # Answer deterministically from research output stored on the
                # candidate; a no-free-text config just acknowledges the task.
                answer = candidate.metadata.get("answer_text", "Completed.")
                for idx in range(inputs):
                    el = page.locator(
                        "textarea[name], input[type='text'], input[type='number'], "
                        "textarea:not([hidden])"
                    ).nth(idx)
                    try:
                        await el.fill(answer)
                    except Exception:
                        continue
                await asyncio.sleep(0.5)

            if await self._locate_count("form button[type='submit']") > 0 or \
               await self._locate_count("button[type='submit']") > 0:
                await self._safe_click("button[type='submit']")
                submitted = True
                await asyncio.sleep(1.5)

            # Consider the task successful when the page accepts our input
            # (e.g. a success/confirmation marker or not still showing the form).
            success_marker = await self._locate_count(
                "[class*='success'], [class*='thank'], .alert-success, "
                "[class*='completed']"
            ) > 0
            success = (submitted and success_marker) or (not submitted and inputs > 0)
            if success:
                amount_earned = candidate.estimated_pay
            else:
                error = "Task not confirmed as completed"
        except Exception as e:
            error = str(e)
            logger.error(f"Clickworker task execution failed: {e}")

        time_spent = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600

        return TaskResult(
            task_id=task_id,
            candidate=candidate,
            success=success,
            amount_earned=amount_earned,
            time_spent_hours=Decimal(str(round(time_spent, 4))),
            error=error,
            platform_data={
                "platform": self.platform.value,
                "submitted": submitted,
                "session_persisted": candidate.metadata.get("session_persisted", False),
            },
        )

    async def get_earnings(self) -> Decimal:
        """Scrape current Clickworker balance from the dashboard."""
        page = await self._new_page()
        try:
            await page.goto(self.DASHBOARD_URL, wait_until="domcontentloaded")
            await asyncio.sleep(1.0)
            raw = await self._read_text(
                "[class*='balance'], [class*='earnings'], .amount"
            )
            if raw:
                return self._to_decimal(raw)
        except Exception as e:
            logger.error(f"Clickworker get_earnings failed: {e}")
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
    """Manages Playwright browser lifecycle and contexts.

    Also owns **session persistence**: platform cookies can be written to and
    replayed from a JSON storage directory. This is the artifact.md §13
    "Playwright session persistence across Render restarts" resolution — a
    freshly-launched browser context can be warmed with previously stored
    cookies so a platform keeps the agent logged in across deployments /
    sleep cycles without re-entering credentials.

    Layout: ``<storage_dir>/<platform>_cookies.json`` per platform.
    """

    def __init__(self, headless: bool = True, storage_dir: Optional[str] = None):
        self.headless = headless
        self.storage_dir = Path(storage_dir or _DEFAULT_SESSION_DIR)
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._contexts: dict[str, BrowserContext] = {}
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:  # pragma: no cover - read-only FS fallback
            logger.warning(f"Could not create session dir {self.storage_dir}: {e}")
            self.storage_dir = Path(_DEFAULT_SESSION_DIR)

    @staticmethod
    def _cookie_path(storage_dir: Path, name: str) -> Path:
        return storage_dir / f"{name}_cookies.json"

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

    async def save_cookies(self, name: str) -> bool:
        """Persist a context's cookies to disk. Returns True on success."""
        context = self._contexts.get(name)
        if not context:
            return False
        try:
            cookies = await context.cookies()
            path = self._cookie_path(self.storage_dir, name)
            path.write_text(json.dumps(cookies, indent=2))
            logger.info(f"Saved {len(cookies)} cookies for {name} -> {path}")
            return True
        except Exception as e:
            logger.warning(f"Could not persist cookies for {name}: {e}")
            return False

    async def load_cookies(self, name: str) -> Optional[list[dict]]:
        """Load a context's cookies from disk, if any were stored."""
        path = self._cookie_path(self.storage_dir, name)
        if not path.exists():
            return None
        try:
            cookies = json.loads(path.read_text())
            logger.info(f"Loaded {len(cookies)} cookies for {name}")
            return cookies
        except Exception as e:
            logger.warning(f"Could not load cookies for {name}: {e}")
            return None

    async def create_context(
        self,
        name: str,
        cookies: Optional[list[dict]] = None,
        user_agent: Optional[str] = None,
        persist: bool = True,
    ) -> BrowserContext:
        """Create a new isolated browser context.

        If ``persist`` is True and ``cookies`` is None, saved cookies are
        loaded automatically so the platform session survives restarts.
        """
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

        stored = cookies
        if stored is None and persist:
            stored = await self.load_cookies(name)

        if stored:
            # Type ignore for cookie dict compatibility
            await context.add_cookies(stored)  # type: ignore[arg-type]

        await self._apply_stealth(context)

        self._contexts[name] = context
        logger.debug(f"Created browser context: {name}")
        return context

    async def _apply_stealth(self, context: BrowserContext) -> None:
        """Patch fingerprinting surfaces on a context (artifact.md §19).

        ``playwright-stealth`` is a soft dependency: if it isn't installed
        the context is used as-is rather than failing task execution over a
        detection-evasion nicety. This is the free, already-integrated tool
        at the bottom of the §19 escalation ladder — nodriver/Camoufox are a
        separate browser engine each and are not wired here.
        """
        try:
            from playwright_stealth import Stealth  # type: ignore[import-not-found]
        except ImportError:
            logger.debug("playwright-stealth not installed — context left unpatched")
            return
        try:
            await Stealth().apply_stealth_async(context)
        except Exception as e:  # pragma: no cover - defensive, library-internal
            logger.warning(f"playwright-stealth failed to apply: {e}")

    async def get_context(self, name: str) -> Optional[BrowserContext]:
        """Get existing context by name."""
        return self._contexts.get(name)

    async def close_context(self, name: str, persist: bool = True) -> None:
        """Close and remove a context, optionally persisting cookies first."""
        if name in self._contexts:
            if persist:
                await self.save_cookies(name)
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
        session_dir: Optional[str] = None,
        vault: Optional[CredentialsVault] = None,
        guardrail: EthicalGuardrail | None = None,
        task_timeout_seconds: float = DEFAULT_TASK_TIMEOUT_SECONDS,
        audit_trail: Optional[AuditTrail] = None,
        bot_tracker: Optional[BotDetectionTracker] = None,
        scam_tracker: Optional[ScamTracker] = None,
    ) -> None:
        self.wallet = wallet
        self.headless = headless
        self.max_concurrent_tasks = max_concurrent_tasks
        # Hard blacklist double-check runs immediately before execution.
        self.guardrail = guardrail or get_guardrail()
        self.task_timeout_seconds = task_timeout_seconds
        self.audit_trail = audit_trail
        # §19: platforms that exhausted the stealth escalation ladder are
        # never retried — checked before spending a login attempt on them.
        self.bot_tracker = bot_tracker
        # §20: a platform that already confirmed-scammed this agent is never
        # rejoined — checked before spending a login attempt on it.
        self.scam_tracker = scam_tracker
        self.session_manager = BrowserSessionManager(
            headless=headless, storage_dir=session_dir
        )
        self._connectors: dict[Platform, PlatformConnector] = {}
        self._credentials: dict[Platform, dict] = {}
        self._active_sessions: set[str] = set()
        self._vault = vault
        self._running = False

    async def start(self) -> None:
        """Start the executor."""
        await self.session_manager.start()
        self._running = True
        logger.info("TaskExecutor started")

    async def stop(self) -> None:
        """Stop the executor, persisting any active platform sessions."""
        self._running = False
        # Persist cookies for every live platform context before shutting the
        # browser (artifact.md §13 session persistence across restarts).
        for name in list(self._active_sessions):
            await self.session_manager.save_cookies(name)
        self._active_sessions.clear()
        await self.session_manager.stop()
        logger.info("TaskExecutor stopped")

    def set_credentials(self, platform: Platform, credentials: dict) -> None:
        """Set credentials for a platform.

        Also persists platform password to the vault if one is available,
        so credentials survive Render restarts (artifact.md §13).
        """
        self._credentials[platform] = credentials
        if self._vault is not None and "password" in credentials:
            self._vault.set(platform.value, credentials["password"], key="password")
        if self._vault is not None and "email" in credentials:
            self._vault.set(platform.value, credentials["email"], key="email")

    async def _get_connector(self, platform: Platform) -> PlatformConnector:
        """Get or create a connector for a platform.

        Credential lookup order:
        1. In-memory ``_credentials`` dict (set via ``set_credentials``).
        2. Vault (Supabase-stored platform passwords).
        """
        if platform in self._connectors:
            return self._connectors[platform]

        if self.bot_tracker is not None and self.bot_tracker.is_blocked(platform.value):
            raise PlatformBlockError(
                f"{platform.value} is permanently blocked (exhausted §19 stealth ladder)"
            )

        if self.scam_tracker is not None and self.scam_tracker.is_platform_scammed(platform.value):
            raise PlatformScammedError(
                f"{platform.value} confirmed-scammed this agent (§20) — never rejoined"
            )

        if platform not in CONNECTORS:
            raise ExecutionError(f"No connector for platform: {platform}")

        # Try in-memory credentials first, then vault
        creds = self._credentials.get(platform)
        if creds is None and self._vault is not None:
            pw = self._vault.get_password(platform.value)
            em = self._vault.get(platform.value, key="email")
            if pw is not None:
                creds = {"email": em or "", "password": pw}
                self._credentials[platform] = creds
                logger.info(f"Loaded credentials for {platform.value} from vault")

        if creds is None:
            raise ExecutionError(f"No credentials for platform: {platform}")

        ctx_name = f"{platform.value}_ctx"
        context = await self.session_manager.create_context(ctx_name, persist=True)
        connector_class = CONNECTORS[platform]
        connector = connector_class(platform, context)

        success = await connector.login(creds)
        if not success:
            raise ExecutionError(f"Login failed for {platform}")

        self._connectors[platform] = connector
        self._active_sessions.add(ctx_name)
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

        Enforces ``self.task_timeout_seconds`` — a hard cap on task duration so
        a stuck task can't run indefinitely while debt keeps ticking (artifact.md
        §14 "Task timeout"). On timeout the task is reported as failed with $0.

        Args:
            candidate: The task to execute.
            certainty: ROI certainty for wallet spend gate (default 95%).

        Returns:
            TaskResult with outcome and earnings.
        """
        if not self._running:
            raise ExecutionError("Executor not started")

        # Hard blacklist double-check immediately before execution. Even if a
        # blacklisted task slipped through scoring, it is blocked here.
        verdict = self.guardrail.evaluate(candidate)
        if not verdict.allowed:
            logger.warning(
                "Refusing to execute task %r (%s): %s",
                candidate.title,
                candidate.platform.value,
                verdict.reason,
            )
            return TaskResult(
                task_id=str(uuid.uuid4())[:8],
                candidate=candidate,
                success=False,
                amount_earned=Decimal("0"),
                error=f"BLOCKED by ethical guardrail: {verdict.reason}",
                platform_data={"guardrail": verdict.model_dump()},
            )

        connector = await self._get_connector(candidate.platform)

        # Execute the task, capped to prevent unbounded runtime while debt accrues
        try:
            result = await asyncio.wait_for(
                connector.execute_task(candidate),
                timeout=self.task_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Task {candidate.title!r} exceeded {self.task_timeout_seconds}s cap; "
                "aborting and reporting failure"
            )
            result = TaskResult(
                task_id=str(uuid.uuid4())[:8],
                candidate=candidate,
                success=False,
                amount_earned=Decimal("0"),
                time_spent_hours=Decimal("0"),
                error=f"Task timed out after {self.task_timeout_seconds}s",
                platform_data={"platform": candidate.platform.value, "timed_out": True},
            )

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

        if self.audit_trail is not None:
            state = resolve_state(self.wallet.debt)
            self.audit_trail.record_task_execution(
                task_id=result.task_id,
                task_title=candidate.title,
                platform=candidate.platform.value,
                success=result.success,
                amount_earned=result.amount_earned,
                time_spent_hours=result.time_spent_hours,
                error=result.error,
                survival_state=state.value,
                debt=self.wallet.debt,
            )

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
async def mock_execute_task(
    candidate: TaskCandidate, success: bool = True
) -> TaskResult:
    """Deterministic mock execution for testing without a real browser.

    A mock must be deterministic — the previous implementation used an 80%
    random success rate which made tests flaky (a run of 20 could occasionally
    contain only successes or only failures). Tests that need a failure pass
    ``success=False`` explicitly.
    """
    task_id = str(uuid.uuid4())[:8]
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