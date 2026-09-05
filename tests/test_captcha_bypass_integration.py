"""Integration tests for the §19 CAPTCHA/bot-detection escalation wiring:

- TaskExecutor._get_connector's vendor-probe + cookie-warm pre-login step
- TaskExecutor._get_connector's post-login failure/success reporting to
  BotDetectionTracker
- PlatformConnector._attempt_captcha_solve
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.captcha_handler import BotDetectionTracker, BotVendor, CaptchaSolveError, StealthTool
from src.persistence import InMemoryStore
from src.task_scorer import Platform


def _mock_wallet():
    wallet = MagicMock()
    wallet.free = MagicMock()
    return wallet


class TestWarmContextForVendor:
    @pytest.mark.asyncio
    async def test_none_vendor_skips_warming(self):
        from src.task_executor import TaskExecutor

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=_mock_wallet(), bot_tracker=BotDetectionTracker(InMemoryStore()))

        with (
            patch("src.task_executor.probe_bot_vendor", AsyncMock(return_value=BotVendor.NONE)),
            patch("src.task_executor.warm_cookies", AsyncMock()) as mock_warm,
        ):
            context = AsyncMock()
            await executor._warm_context_for_vendor(Platform.CLICKWORKER, "https://x.example/login", context)

        mock_warm.assert_not_called()
        context.add_cookies.assert_not_called()

    @pytest.mark.asyncio
    async def test_datadome_vendor_warms_and_adds_cookies_on_first_attempt(self):
        """DATADOME's ladder starts at Camoufox (a warmer), unlike CLOUDFLARE
        which starts at playwright_stealth (not a warmer)."""
        from src.task_executor import TaskExecutor

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=_mock_wallet(), bot_tracker=BotDetectionTracker(InMemoryStore()))

        cookies = [{"name": "datadome", "value": "abc", "domain": ".x.example", "path": "/"}]
        with (
            patch("src.task_executor.probe_bot_vendor", AsyncMock(return_value=BotVendor.DATADOME)),
            patch("src.task_executor.warm_cookies", AsyncMock(return_value=cookies)) as mock_warm,
        ):
            context = AsyncMock()
            await executor._warm_context_for_vendor(Platform.CLICKWORKER, "https://x.example/login", context)

        mock_warm.assert_awaited_once_with(StealthTool.CAMOUFOX, "https://x.example/login")
        context.add_cookies.assert_awaited_once_with(cookies)

    @pytest.mark.asyncio
    async def test_cloudflare_first_attempt_uses_stealth_not_a_warmer(self):
        from src.task_executor import TaskExecutor

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=_mock_wallet(), bot_tracker=BotDetectionTracker(InMemoryStore()))

        with (
            patch("src.task_executor.probe_bot_vendor", AsyncMock(return_value=BotVendor.CLOUDFLARE)),
            patch("src.task_executor.warm_cookies", AsyncMock()) as mock_warm,
        ):
            context = AsyncMock()
            await executor._warm_context_for_vendor(Platform.CLICKWORKER, "https://x.example/login", context)

        mock_warm.assert_not_called()
        context.add_cookies.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_attempt_escalates_to_nodriver(self):
        """First-attempt tool for CLOUDFLARE is playwright_stealth (not a
        warmer, so warm_cookies isn't invoked for it); after one recorded
        failure the ladder should move to nodriver, which IS a warmer."""
        from src.task_executor import TaskExecutor

        tracker = BotDetectionTracker(InMemoryStore())
        tracker.record_failure(Platform.CLICKWORKER.value, BotVendor.CLOUDFLARE)

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=_mock_wallet(), bot_tracker=tracker)

        with (
            patch("src.task_executor.probe_bot_vendor", AsyncMock(return_value=BotVendor.CLOUDFLARE)),
            patch("src.task_executor.warm_cookies", AsyncMock(return_value=[])) as mock_warm,
        ):
            context = AsyncMock()
            await executor._warm_context_for_vendor(Platform.CLICKWORKER, "https://x.example/login", context)

        mock_warm.assert_awaited_once_with(StealthTool.NODRIVER, "https://x.example/login")

    @pytest.mark.asyncio
    async def test_warming_failure_does_not_raise(self):
        """A cookie-warm exception must not block the fallback plain-Playwright attempt."""
        from src.task_executor import TaskExecutor

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=_mock_wallet(), bot_tracker=BotDetectionTracker(InMemoryStore()))

        with (
            patch("src.task_executor.probe_bot_vendor", AsyncMock(return_value=BotVendor.CLOUDFLARE)),
            patch("src.task_executor.warm_cookies", AsyncMock(side_effect=RuntimeError("browser crashed"))),
        ):
            context = AsyncMock()
            await executor._warm_context_for_vendor(Platform.CLICKWORKER, "https://x.example/login", context)

        context.add_cookies.assert_not_called()


class TestGetConnectorEscalationReporting:
    @pytest.mark.asyncio
    async def test_login_failure_records_failure_with_probed_vendor(self):
        from src.task_executor import ExecutionError, TaskExecutor

        tracker = BotDetectionTracker(InMemoryStore())
        mock_connector = AsyncMock()
        mock_connector.login = AsyncMock(return_value=False)

        class _FakeConnectorClass:
            LOGIN_URL = "https://x.example/login"

            def __new__(cls, platform, credentials):
                return mock_connector

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=_mock_wallet(), bot_tracker=tracker)
            executor.session_manager.create_context = AsyncMock(return_value=AsyncMock())

        executor.set_credentials(Platform.CLICKWORKER, {"email": "e", "password": "p"})

        with (
            patch("src.task_executor.CONNECTORS", {Platform.CLICKWORKER: _FakeConnectorClass}),
            patch("src.task_executor.probe_bot_vendor", AsyncMock(return_value=BotVendor.NONE)),
            patch("src.task_executor.warm_cookies", AsyncMock(return_value=[])),
        ):
            with pytest.raises(ExecutionError):
                await executor._get_connector(Platform.CLICKWORKER)

        # A failure was recorded for clickworker (attempt count advanced).
        assert tracker.next_tool(Platform.CLICKWORKER.value, BotVendor.NONE) is StealthTool.NODRIVER

    @pytest.mark.asyncio
    async def test_login_success_resets_tracker(self):
        from src.task_executor import TaskExecutor

        tracker = BotDetectionTracker(InMemoryStore())
        tracker.record_failure(Platform.CLICKWORKER.value, BotVendor.NONE)
        mock_connector = AsyncMock()
        mock_connector.login = AsyncMock(return_value=True)

        with patch("src.task_executor.BrowserSessionManager"):
            executor = TaskExecutor(wallet=_mock_wallet(), bot_tracker=tracker)
            executor.session_manager.create_context = AsyncMock(return_value=AsyncMock())

        executor.set_credentials(Platform.CLICKWORKER, {"email": "e", "password": "p"})

        with patch("src.task_executor.CONNECTORS", {Platform.CLICKWORKER: lambda p, c: mock_connector}):
            await executor._get_connector(Platform.CLICKWORKER)

        assert tracker.next_tool(Platform.CLICKWORKER.value, BotVendor.NONE) is StealthTool.PLAYWRIGHT_STEALTH


class _FakeLocator:
    def __init__(self, count=0, sitekey=None):
        self._count = count
        self._sitekey = sitekey

    async def count(self):
        return self._count

    @property
    def first(self):
        return self

    async def get_attribute(self, name):
        return self._sitekey


class _FakePage:
    def __init__(self, recaptcha_count=0, sitekey="site-key-123", evaluate_ok=True):
        self.url = "https://x.example/login"
        self._recaptcha_count = recaptcha_count
        self._sitekey = sitekey
        self._evaluate_ok = evaluate_ok
        self.evaluate_calls: list[tuple] = []

    def locator(self, selector):
        if "recaptcha" in selector:
            return _FakeLocator(self._recaptcha_count, self._sitekey)
        if "data-sitekey" in selector:
            return _FakeLocator(1, self._sitekey)
        return _FakeLocator(0)

    async def evaluate(self, script, *args):
        self.evaluate_calls.append(args)
        if not self._evaluate_ok:
            raise RuntimeError("evaluate failed")


class TestAttemptCaptchaSolve:
    @pytest.mark.asyncio
    async def test_no_widget_present_returns_false(self):
        from src.task_executor import ClickworkerConnector

        connector = ClickworkerConnector.__new__(ClickworkerConnector)
        connector.platform = Platform.CLICKWORKER
        connector.page = _FakePage(recaptcha_count=0)

        assert await connector._attempt_captcha_solve() is False

    @pytest.mark.asyncio
    async def test_solves_recaptcha_and_injects_token(self):
        from src.task_executor import ClickworkerConnector

        connector = ClickworkerConnector.__new__(ClickworkerConnector)
        connector.platform = Platform.CLICKWORKER
        connector.page = _FakePage(recaptcha_count=1, sitekey="my-sitekey")

        with patch("src.captcha_handler.solve_recaptcha_v2", AsyncMock(return_value="token-abc")):
            solved = await connector._attempt_captcha_solve()

        assert solved is True
        assert connector.page.evaluate_calls  # token was injected

    @pytest.mark.asyncio
    async def test_no_sitekey_returns_false(self):
        from src.task_executor import ClickworkerConnector

        connector = ClickworkerConnector.__new__(ClickworkerConnector)
        connector.platform = Platform.CLICKWORKER
        connector.page = _FakePage(recaptcha_count=1, sitekey=None)

        assert await connector._attempt_captcha_solve() is False

    @pytest.mark.asyncio
    async def test_solve_error_returns_false(self):
        from src.task_executor import ClickworkerConnector

        connector = ClickworkerConnector.__new__(ClickworkerConnector)
        connector.platform = Platform.CLICKWORKER
        connector.page = _FakePage(recaptcha_count=1, sitekey="my-sitekey")

        with patch(
            "src.captcha_handler.solve_recaptcha_v2",
            AsyncMock(side_effect=CaptchaSolveError("no balance")),
        ):
            assert await connector._attempt_captcha_solve() is False

    @pytest.mark.asyncio
    async def test_no_page_returns_false(self):
        from src.task_executor import ClickworkerConnector

        connector = ClickworkerConnector.__new__(ClickworkerConnector)
        connector.platform = Platform.CLICKWORKER
        connector.page = None

        assert await connector._attempt_captcha_solve() is False
