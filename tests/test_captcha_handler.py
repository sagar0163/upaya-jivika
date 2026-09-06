"""Tests for src/captcha_handler.py — bot-detection strategy (artifact.md §19)."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.captcha_handler import (
    TOOL_IMPLEMENTED,
    BotDetectionTracker,
    BotVendor,
    CaptchaSolveError,
    PlatformBlockError,
    StealthTool,
    _anticaptcha_solver,
    _nodriver_cookies_to_playwright,
    anticaptcha_api_key,
    detect_bot_vendor,
    human_delay,
    human_type,
    probe_bot_vendor,
    recommend_tool,
    solve_hcaptcha,
    solve_recaptcha_v2,
    solve_turnstile,
    twocaptcha_api_key,
    warm_cookies,
)
from src.persistence import InMemoryStore


class TestDetectBotVendor:
    def test_cloudflare_detected_via_cf_ray(self):
        assert detect_bot_vendor({"CF-RAY": "abc123"}) is BotVendor.CLOUDFLARE

    def test_cloudflare_header_case_insensitive(self):
        assert detect_bot_vendor({"cf-ray": "abc123"}) is BotVendor.CLOUDFLARE

    def test_datadome_detected_on_403_with_header(self):
        vendor = detect_bot_vendor({"X-DataDome-Blocked": "1"}, status_code=403)
        assert vendor is BotVendor.DATADOME

    def test_datadome_not_detected_without_403(self):
        vendor = detect_bot_vendor({"X-DataDome-Blocked": "1"}, status_code=200)
        assert vendor is not BotVendor.DATADOME

    def test_akamai_detected(self):
        assert detect_bot_vendor({"akamai-grn": "xyz"}) is BotVendor.AKAMAI

    def test_kasada_detected_bare_403_no_headers(self):
        assert detect_bot_vendor({}, status_code=403) is BotVendor.KASADA

    def test_kasada_detected_bare_429_no_headers(self):
        assert detect_bot_vendor({}, status_code=429) is BotVendor.KASADA

    def test_no_vendor_on_clean_200(self):
        assert detect_bot_vendor({"Content-Type": "text/html"}, status_code=200) is BotVendor.NONE

    def test_no_vendor_on_404_with_no_headers(self):
        # A plain 404 with no headers isn't a bot-block signature.
        assert detect_bot_vendor({}, status_code=404) is BotVendor.NONE


class TestRecommendTool:
    def test_first_attempt_none_vendor_uses_playwright_stealth(self):
        assert recommend_tool(BotVendor.NONE, 0) is StealthTool.PLAYWRIGHT_STEALTH

    def test_escalates_through_ladder(self):
        tools_seen = []
        for attempt in range(4):
            tools_seen.append(recommend_tool(BotVendor.CLOUDFLARE, attempt))
        assert tools_seen == [
            StealthTool.PLAYWRIGHT_STEALTH,
            StealthTool.NODRIVER,
            StealthTool.CAMOUFOX,
            StealthTool.GIVE_UP,
        ]

    def test_kasada_gives_up_immediately(self):
        assert recommend_tool(BotVendor.KASADA, 0) is StealthTool.GIVE_UP

    def test_negative_attempts_raises(self):
        with pytest.raises(ValueError):
            recommend_tool(BotVendor.NONE, -1)

    def test_datadome_ladder_uses_camoufox_first(self):
        assert recommend_tool(BotVendor.DATADOME, 0) is StealthTool.CAMOUFOX


class TestHumanDelay:
    @pytest.mark.asyncio
    async def test_delay_within_bounds(self):
        start = time.monotonic()
        await human_delay(min_ms=10, max_ms=20)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert 8 <= elapsed_ms <= 200  # generous upper bound for CI jitter

    @pytest.mark.asyncio
    async def test_invalid_bounds_raise(self):
        with pytest.raises(ValueError):
            await human_delay(min_ms=100, max_ms=10)

    @pytest.mark.asyncio
    async def test_negative_min_raises(self):
        with pytest.raises(ValueError):
            await human_delay(min_ms=-5, max_ms=10)


class _FakeKeyboard:
    def __init__(self):
        self.typed: list[str] = []

    async def type(self, text: str) -> None:
        self.typed.append(text)


class _FakePage:
    def __init__(self):
        self.keyboard = _FakeKeyboard()
        self.clicked: list[str] = []

    async def click(self, selector: str) -> None:
        self.clicked.append(selector)


class TestHumanType:
    @pytest.mark.asyncio
    async def test_clicks_then_types_each_character(self):
        page = _FakePage()
        await human_type(page, "#email", "hi", min_keystroke_ms=1, max_keystroke_ms=2)
        assert page.clicked == ["#email"]
        assert page.keyboard.typed == ["h", "i"]

    @pytest.mark.asyncio
    async def test_empty_string_still_clicks(self):
        page = _FakePage()
        await human_type(page, "#email", "", min_keystroke_ms=1, max_keystroke_ms=2)
        assert page.clicked == ["#email"]
        assert page.keyboard.typed == []


class TestBotDetectionTracker:
    def test_next_tool_starts_at_ladder_front(self):
        tracker = BotDetectionTracker(InMemoryStore())
        assert tracker.next_tool("clickworker", BotVendor.NONE) is StealthTool.PLAYWRIGHT_STEALTH

    def test_record_failure_escalates(self):
        tracker = BotDetectionTracker(InMemoryStore())
        tool = tracker.record_failure("clickworker", BotVendor.CLOUDFLARE)
        assert tool is StealthTool.NODRIVER
        assert tracker.next_tool("clickworker", BotVendor.CLOUDFLARE) is StealthTool.NODRIVER

    def test_permanently_blocks_after_max_attempts(self):
        store = InMemoryStore()
        tracker = BotDetectionTracker(store)
        for _ in range(BotDetectionTracker.MAX_ATTEMPTS):
            tracker.record_failure("badplatform.io", BotVendor.CLOUDFLARE, reason="captcha wall")

        assert tracker.is_blocked("badplatform.io") is True
        assert store.is_platform_blocked("badplatform.io") is True

    def test_blocks_when_ladder_exhausted_before_max_attempts(self):
        # Kasada's ladder is empty, so a single failure should block immediately.
        store = InMemoryStore()
        tracker = BotDetectionTracker(store)
        tool = tracker.record_failure("kasada-site.io", BotVendor.KASADA)
        assert tool is StealthTool.GIVE_UP
        assert tracker.is_blocked("kasada-site.io") is True

    def test_blocked_platform_raises_on_next_tool(self):
        store = InMemoryStore()
        tracker = BotDetectionTracker(store)
        store.mark_platform_blocked("dead.io", {"vendor": "kasada"})

        with pytest.raises(PlatformBlockError):
            tracker.next_tool("dead.io", BotVendor.KASADA)

    def test_record_success_resets_attempts(self):
        tracker = BotDetectionTracker(InMemoryStore())
        tracker.record_failure("clickworker", BotVendor.CLOUDFLARE)
        tracker.record_success("clickworker")
        # Back to the front of the ladder, not escalated.
        assert tracker.next_tool("clickworker", BotVendor.CLOUDFLARE) is StealthTool.PLAYWRIGHT_STEALTH

    def test_unrelated_platforms_tracked_independently(self):
        tracker = BotDetectionTracker(InMemoryStore())
        tracker.record_failure("platform_a", BotVendor.CLOUDFLARE)
        assert tracker.next_tool("platform_b", BotVendor.CLOUDFLARE) is StealthTool.PLAYWRIGHT_STEALTH


class TestToolImplemented:
    def test_all_ladder_tools_marked_implemented(self):
        """§19 upgrade: nodriver/Camoufox/2Captcha are real now, not stubs."""
        assert TOOL_IMPLEMENTED[StealthTool.NODRIVER] is True
        assert TOOL_IMPLEMENTED[StealthTool.CAMOUFOX] is True
        assert TOOL_IMPLEMENTED[StealthTool.PAID_CAPTCHA_SOLVER] is True


class TestTwocaptchaApiKey:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
        assert twocaptcha_api_key() is None

    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("TWOCAPTCHA_API_KEY", "abc123")
        assert twocaptcha_api_key() == "abc123"

    def test_empty_string_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("TWOCAPTCHA_API_KEY", "")
        assert twocaptcha_api_key() is None


class TestAnticaptchaApiKey:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("ANTICAPTCHA_API_KEY", raising=False)
        assert anticaptcha_api_key() is None

    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("ANTICAPTCHA_API_KEY", "anti123")
        assert anticaptcha_api_key() == "anti123"

    def test_empty_string_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("ANTICAPTCHA_API_KEY", "")
        assert anticaptcha_api_key() is None


class TestProbeBotVendor:
    @pytest.mark.asyncio
    async def test_classifies_cloudflare_response(self):
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(200, headers={"CF-RAY": "abc"}, request=request)
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=response)):
            vendor = await probe_bot_vendor("https://example.com")
        assert vendor is BotVendor.CLOUDFLARE

    @pytest.mark.asyncio
    async def test_classifies_clean_response_as_none(self):
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(200, headers={}, request=request)
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=response)):
            vendor = await probe_bot_vendor("https://example.com")
        assert vendor is BotVendor.NONE

    @pytest.mark.asyncio
    async def test_network_failure_is_treated_as_none_not_raised(self):
        with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectError("down"))):
            vendor = await probe_bot_vendor("https://unreachable.example")
        assert vendor is BotVendor.NONE


class _FakeCookie:
    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "sid")
        self.value = kwargs.get("value", "abc")
        self.domain = kwargs.get("domain", ".example.com")
        self.path = kwargs.get("path", "/")
        self.http_only = kwargs.get("http_only", True)
        self.secure = kwargs.get("secure", True)
        self.expires = kwargs.get("expires", 0)
        self.same_site = kwargs.get("same_site", None)


class TestNodriverCookieConversion:
    def test_converts_basic_fields(self):
        cookies = _nodriver_cookies_to_playwright([_FakeCookie()])
        assert cookies == [{
            "name": "sid",
            "value": "abc",
            "domain": ".example.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
        }]

    def test_includes_expires_when_positive(self):
        cookies = _nodriver_cookies_to_playwright([_FakeCookie(expires=123456.0)])
        assert cookies[0]["expires"] == 123456.0

    def test_omits_expires_when_zero(self):
        cookies = _nodriver_cookies_to_playwright([_FakeCookie(expires=0)])
        assert "expires" not in cookies[0]

    def test_includes_valid_same_site(self):
        cookies = _nodriver_cookies_to_playwright([_FakeCookie(same_site="Lax")])
        assert cookies[0]["sameSite"] == "Lax"

    def test_omits_invalid_same_site(self):
        cookies = _nodriver_cookies_to_playwright([_FakeCookie(same_site="Bogus")])
        assert "sameSite" not in cookies[0]

    def test_empty_path_defaults_to_slash(self):
        cookies = _nodriver_cookies_to_playwright([_FakeCookie(path="")])
        assert cookies[0]["path"] == "/"


class TestWarmCookies:
    @pytest.mark.asyncio
    async def test_dispatches_to_nodriver(self):
        with patch(
            "src.captcha_handler.warm_with_nodriver", AsyncMock(return_value=[{"name": "a"}])
        ) as mock_warm:
            cookies = await warm_cookies(StealthTool.NODRIVER, "https://x.example")
        mock_warm.assert_awaited_once_with("https://x.example")
        assert cookies == [{"name": "a"}]

    @pytest.mark.asyncio
    async def test_dispatches_to_camoufox(self):
        with patch(
            "src.captcha_handler.warm_with_camoufox", AsyncMock(return_value=[{"name": "b"}])
        ) as mock_warm:
            cookies = await warm_cookies(StealthTool.CAMOUFOX, "https://x.example")
        mock_warm.assert_awaited_once_with("https://x.example")
        assert cookies == [{"name": "b"}]

    @pytest.mark.asyncio
    async def test_raises_for_non_warmer_tool(self):
        with pytest.raises(ValueError):
            await warm_cookies(StealthTool.PLAYWRIGHT_STEALTH, "https://x.example")


class TestSolveRecaptchaV2:
    @pytest.mark.asyncio
    async def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
        monkeypatch.delenv("ANTICAPTCHA_API_KEY", raising=False)
        with pytest.raises(CaptchaSolveError, match="not configured"):
            await solve_recaptcha_v2("sitekey", "https://x.example")

    @pytest.mark.asyncio
    async def test_returns_token_on_success(self, monkeypatch):
        monkeypatch.setenv("TWOCAPTCHA_API_KEY", "key123")
        mock_solver = MagicMock()
        mock_solver.recaptcha.return_value = {"code": "solved-token"}
        with patch("twocaptcha.TwoCaptcha", return_value=mock_solver):
            token = await solve_recaptcha_v2("sitekey", "https://x.example")
        assert token == "solved-token"
        mock_solver.recaptcha.assert_called_once_with(sitekey="sitekey", url="https://x.example")

    @pytest.mark.asyncio
    async def test_wraps_api_exception(self, monkeypatch):
        from twocaptcha import ApiException

        monkeypatch.setenv("TWOCAPTCHA_API_KEY", "key123")
        monkeypatch.delenv("ANTICAPTCHA_API_KEY", raising=False)
        mock_solver = MagicMock()
        mock_solver.recaptcha.side_effect = ApiException("ERROR_ZERO_BALANCE")
        with patch("twocaptcha.TwoCaptcha", return_value=mock_solver):
            with pytest.raises(CaptchaSolveError):
                await solve_recaptcha_v2("sitekey", "https://x.example")

    @pytest.mark.asyncio
    async def test_falls_back_to_anticaptcha_when_2captcha_unconfigured(self, monkeypatch):
        monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
        monkeypatch.setenv("ANTICAPTCHA_API_KEY", "anti-key")
        mock_solver = MagicMock()
        mock_solver.solve_and_return_solution.return_value = "ac-token"
        with patch("anticaptchaofficial.recaptchav2proxyless.recaptchaV2Proxyless", return_value=mock_solver):
            token = await solve_recaptcha_v2("sitekey", "https://x.example")
        assert token == "ac-token"
        mock_solver.set_key.assert_called_once_with("anti-key")
        mock_solver.set_website_url.assert_called_once_with("https://x.example")
        mock_solver.set_website_key.assert_called_once_with("sitekey")
        mock_solver.solve_and_return_solution.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_anticaptcha_when_2captcha_fails(self, monkeypatch):
        from twocaptcha import ApiException

        monkeypatch.setenv("TWOCAPTCHA_API_KEY", "key123")
        monkeypatch.setenv("ANTICAPTCHA_API_KEY", "anti-key")
        mock_2c = MagicMock()
        mock_2c.recaptcha.side_effect = ApiException("ERROR_CAPTCHA_UNSOLVABLE")
        mock_ac = MagicMock()
        mock_ac.solve_and_return_solution.return_value = "ac-token"
        with (
            patch("twocaptcha.TwoCaptcha", return_value=mock_2c),
            patch("anticaptchaofficial.recaptchav2proxyless.recaptchaV2Proxyless", return_value=mock_ac),
        ):
            token = await solve_recaptcha_v2("sitekey", "https://x.example")
        assert token == "ac-token"
        mock_ac.solve_and_return_solution.assert_called_once()

    @pytest.mark.asyncio
    async def test_anticaptcha_failure_raises_with_error_code(self, monkeypatch):
        monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
        monkeypatch.setenv("ANTICAPTCHA_API_KEY", "anti-key")
        mock_ac = MagicMock()
        mock_ac.solve_and_return_solution.return_value = 0
        mock_ac.error_code = "ERROR_EMPTY_ACTION"
        with patch("anticaptchaofficial.recaptchav2proxyless.recaptchaV2Proxyless", return_value=mock_ac):
            with pytest.raises(CaptchaSolveError, match="ERROR_EMPTY_ACTION"):
                await solve_recaptcha_v2("sitekey", "https://x.example")

    @pytest.mark.asyncio
    async def test_raises_with_aggregate_errors_when_all_solvers_fail(self, monkeypatch):
        from twocaptcha import ApiException

        monkeypatch.setenv("TWOCAPTCHA_API_KEY", "key123")
        monkeypatch.setenv("ANTICAPTCHA_API_KEY", "anti-key")
        mock_2c = MagicMock()
        mock_2c.recaptcha.side_effect = ApiException("ERROR_ZERO_BALANCE")
        mock_ac = MagicMock()
        mock_ac.solve_and_return_solution.return_value = 0
        mock_ac.error_code = "ERROR_ZERO_BALANCE"
        with (
            patch("twocaptcha.TwoCaptcha", return_value=mock_2c),
            patch("anticaptchaofficial.recaptchav2proxyless.recaptchaV2Proxyless", return_value=mock_ac),
        ):
            with pytest.raises(CaptchaSolveError, match="All solvers failed"):
                await solve_recaptcha_v2("sitekey", "https://x.example")

    def test_unknown_anticaptcha_method_raises(self):
        with pytest.raises(ValueError, match="Unknown anticaptcha method"):
            _anticaptcha_solver("geetest")


class TestSolveHcaptcha:
    @pytest.mark.asyncio
    async def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
        monkeypatch.delenv("ANTICAPTCHA_API_KEY", raising=False)
        with pytest.raises(CaptchaSolveError, match="not configured"):
            await solve_hcaptcha("sitekey", "https://x.example")

    @pytest.mark.asyncio
    async def test_returns_token_on_success(self, monkeypatch):
        monkeypatch.setenv("TWOCAPTCHA_API_KEY", "key123")
        mock_solver = MagicMock()
        mock_solver.hcaptcha.return_value = {"code": "hc-token"}
        with patch("twocaptcha.TwoCaptcha", return_value=mock_solver):
            token = await solve_hcaptcha("sitekey", "https://x.example")
        assert token == "hc-token"

    @pytest.mark.asyncio
    async def test_falls_back_to_anticaptcha_via_hcaptcha_solver(self, monkeypatch):
        monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
        monkeypatch.setenv("ANTICAPTCHA_API_KEY", "anti-key")
        mock_ac = MagicMock()
        mock_ac.solve_and_return_solution.return_value = "hc-ac-token"
        with patch("anticaptchaofficial.hcaptchaproxyless.hCaptchaProxyless", return_value=mock_ac):
            token = await solve_hcaptcha("sitekey", "https://x.example")
        assert token == "hc-ac-token"


class TestSolveTurnstile:
    @pytest.mark.asyncio
    async def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
        monkeypatch.delenv("ANTICAPTCHA_API_KEY", raising=False)
        with pytest.raises(CaptchaSolveError, match="not configured"):
            await solve_turnstile("sitekey", "https://x.example")

    @pytest.mark.asyncio
    async def test_returns_token_on_success_twocaptcha(self, monkeypatch):
        monkeypatch.setenv("TWOCAPTCHA_API_KEY", "key123")
        monkeypatch.delenv("ANTICAPTCHA_API_KEY", raising=False)
        mock_solver = MagicMock()
        mock_solver.turnstile.return_value = {"code": "ts-token"}
        with patch("twocaptcha.TwoCaptcha", return_value=mock_solver):
            token = await solve_turnstile("sitekey", "https://x.example")
        assert token == "ts-token"
        mock_solver.turnstile.assert_called_once_with(sitekey="sitekey", url="https://x.example")

    @pytest.mark.asyncio
    async def test_returns_token_on_success_anticaptcha(self, monkeypatch):
        monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
        monkeypatch.setenv("ANTICAPTCHA_API_KEY", "key123")
        mock_solver = MagicMock()
        mock_solver.solve_and_return_solution.return_value = "ts-token"
        with patch("anticaptchaofficial.turnstileproxyless.turnstileProxyless", return_value=mock_solver):
            token = await solve_turnstile("sitekey", "https://x.example")
        assert token == "ts-token"
        mock_solver.set_key.assert_called_once_with("key123")
        mock_solver.set_website_url.assert_called_once_with("https://x.example")
        mock_solver.set_website_key.assert_called_once_with("sitekey")
        mock_solver.solve_and_return_solution.assert_called_once()
