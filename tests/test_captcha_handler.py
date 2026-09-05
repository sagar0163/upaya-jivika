"""Tests for src/captcha_handler.py — bot-detection strategy (artifact.md §19)."""

import time

import pytest

from src.captcha_handler import (
    BotDetectionTracker,
    BotVendor,
    PlatformBlockError,
    StealthTool,
    detect_bot_vendor,
    human_delay,
    human_type,
    recommend_tool,
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
