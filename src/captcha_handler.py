"""CAPTCHA & bot-detection strategy (artifact.md §19).

Implements the parts of §19 that are deterministic and testable without a
live browser:

- **Vendor probing** — read response headers/status from a plain GET before
  attempting any bypass, and classify which anti-bot vendor (if any) is in
  front of a platform.
- **Escalation ladder** — pick the next stealth tool to try given the vendor
  and how many attempts have already failed, without ever repeating a tool
  that already lost.
- **Behavioral simulation** — human-paced delay/typing helpers so browser
  automation doesn't move at inhuman, detectable speed.
- **Permanent block tracking** — once a platform has exhausted the ladder, it
  is recorded (via the persistence layer, §20-style permanent memory) so the
  agent never wastes debt-time retrying a dead end, across restarts and
  across lives.

- **nodriver cookie-warming** — nodriver drives Chrome directly over the CDP
  protocol rather than through Playwright's injected automation bindings,
  which is what lets it clear checks (``navigator.webdriver``, CDP-detection
  probes) that flag a regular Playwright session. It is used purely as a
  cookie warmer: visit the gated URL with nodriver until a challenge clears,
  export the resulting session cookies, then hand them to the existing
  Playwright context via ``add_cookies`` so the rest of a connector's
  selector/typing logic keeps running on the one already-built engine
  instead of duplicating it for a second one.
- **Camoufox** — a patched-Firefox engine exposed through a genuine
  Playwright-compatible API (``AsyncCamoufox`` yields a real
  ``playwright.async_api.Browser``), used the same cookie-warming way as
  nodriver for vendors nodriver alone doesn't clear.
- **2Captcha paid solving** — for an inline reCAPTCHA/hCaptcha widget that
  survives stealth, ``solve_recaptcha_v2``/``solve_hcaptcha`` submit the
  sitekey to the 2Captcha API and return the response token to inject into
  the page. Soft-configured via ``TWOCAPTCHA_API_KEY`` — absent means this
  rung is skipped, same fail-soft pattern as the rest of the codebase's
  optional secrets.

Kasada has no ladder entry (artifact.md §19: "hardest — research
alternative") — none of the above reliably clears it, so a Kasada-fronted
platform still just gets blocklisted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from enum import Enum
from typing import Any, Protocol, cast

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vendor detection
# ---------------------------------------------------------------------------

class BotVendor(str, Enum):
    """Anti-bot vendor identified from a probe response."""

    CLOUDFLARE = "cloudflare"
    DATADOME = "datadome"
    AKAMAI = "akamai"
    KASADA = "kasada"
    NONE = "none"


def detect_bot_vendor(headers: dict[str, str], status_code: int = 200) -> BotVendor:
    """Classify the anti-bot vendor guarding a platform from a probe response.

    Mirrors the artifact.md §19 probe-flow table. Header lookups are
    case-insensitive since HTTP header casing is not guaranteed to survive
    every client/proxy.
    """
    lower_headers = {k.lower(): v for k, v in headers.items()}

    if "cf-ray" in lower_headers:
        return BotVendor.CLOUDFLARE
    if status_code == 403 and any(k.startswith("x-datadome") for k in lower_headers):
        return BotVendor.DATADOME
    if "akamai-grn" in lower_headers:
        return BotVendor.AKAMAI
    if status_code in (403, 429) and not lower_headers:
        # No vendor signature at all on a hard block — Kasada tends to return
        # bare 403/429s with minimal headers and no body.
        return BotVendor.KASADA
    return BotVendor.NONE


async def probe_bot_vendor(url: str, *, timeout: float = 10.0) -> BotVendor:
    """Plain (non-browser) GET against ``url``, classified via :func:`detect_bot_vendor`.

    Run before spending a browser context/login attempt on a platform, so
    the escalation ladder can pick the right tool up front instead of
    discovering the vendor only after Playwright's own attempt fails. A
    network failure classifies as :attr:`BotVendor.NONE` — probing is a
    cheap optimization, not a security boundary, so a probe error should
    never block the real (browser-based) attempt from proceeding.
    """
    import httpx

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(url)
        return detect_bot_vendor(dict(response.headers), response.status_code)
    except Exception:
        logger.warning("Bot-vendor probe failed for %s", url, exc_info=True)
        return BotVendor.NONE


# ---------------------------------------------------------------------------
# Escalation ladder
# ---------------------------------------------------------------------------

class StealthTool(str, Enum):
    """A stealth tool the agent can reach for, in escalation order."""

    PLAYWRIGHT_STEALTH = "playwright_stealth"
    NODRIVER = "nodriver"
    CAMOUFOX = "camoufox"
    PAID_CAPTCHA_SOLVER = "2captcha"
    GIVE_UP = "give_up"


#: Which tools are actually wired into task_executor.py today. ``True`` means
#: the code path exists and will be attempted; it does not guarantee success
#: against a given vendor, and PAID_CAPTCHA_SOLVER additionally soft-degrades
#: to a no-op if ``TWOCAPTCHA_API_KEY`` isn't configured (see
#: :func:`twocaptcha_api_key`).
TOOL_IMPLEMENTED: dict[StealthTool, bool] = {
    StealthTool.PLAYWRIGHT_STEALTH: True,
    StealthTool.NODRIVER: True,
    StealthTool.CAMOUFOX: True,
    StealthTool.PAID_CAPTCHA_SOLVER: True,
    StealthTool.GIVE_UP: True,
}


def twocaptcha_api_key() -> str | None:
    """Return the configured 2Captcha API key, or ``None`` if unset."""
    return os.environ.get("TWOCAPTCHA_API_KEY") or None

#: Escalation ladder per vendor, most-likely-to-work first, as designed in
#: artifact.md §19's "stealth toolkit". ``NONE`` (no detected vendor) starts
#: at the lightest tool since heavier tools are unnecessary overhead.
_LADDER: dict[BotVendor, list[StealthTool]] = {
    BotVendor.NONE: [StealthTool.PLAYWRIGHT_STEALTH, StealthTool.NODRIVER],
    BotVendor.CLOUDFLARE: [StealthTool.PLAYWRIGHT_STEALTH, StealthTool.NODRIVER, StealthTool.CAMOUFOX],
    BotVendor.DATADOME: [StealthTool.CAMOUFOX, StealthTool.NODRIVER],
    BotVendor.AKAMAI: [StealthTool.CAMOUFOX],
    BotVendor.KASADA: [],  # artifact.md §19: "hardest — research alternative"
}


def recommend_tool(vendor: BotVendor, attempts_so_far: int) -> StealthTool:
    """Return the next tool to try, given how many prior attempts failed.

    ``attempts_so_far`` is the count of *already-failed* attempts for this
    platform (0 on the first try). Returns :attr:`StealthTool.GIVE_UP` once
    the ladder for this vendor is exhausted.
    """
    ladder = _LADDER.get(vendor, [])
    if attempts_so_far < 0:
        raise ValueError("attempts_so_far must be >= 0")
    if attempts_so_far >= len(ladder):
        return StealthTool.GIVE_UP
    return ladder[attempts_so_far]


# ---------------------------------------------------------------------------
# nodriver / Camoufox cookie warming
#
# Both tools solve the same problem the same way: launch an engine that
# clears a given anti-bot vendor's checks, let the challenge resolve, export
# the resulting session cookies in Playwright's ``add_cookies`` shape, then
# hand off to the existing Playwright-based connector. Neither engine's page-
# interaction API is used beyond that handoff point — duplicating selector
# logic per engine isn't worth it when the one already built keeps working
# once it holds a legitimate cookie jar.
# ---------------------------------------------------------------------------

#: Playwright's ``BrowserContext.add_cookies`` cookie dict shape.
CookieDict = dict[str, Any]


def _same_site_value(raw: Any) -> str | None:
    """Normalize a CDP ``CookieSameSite`` value to Playwright's expected string."""
    value = getattr(raw, "value", raw)
    return value if value in ("Strict", "Lax", "None") else None


def _nodriver_cookies_to_playwright(cookies: list[Any]) -> list[CookieDict]:
    out: list[CookieDict] = []
    for c in cookies:
        entry: CookieDict = {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path or "/",
            "httpOnly": bool(c.http_only),
            "secure": bool(c.secure),
        }
        if c.expires and c.expires > 0:
            entry["expires"] = c.expires
        same_site = _same_site_value(c.same_site)
        if same_site is not None:
            entry["sameSite"] = same_site
        out.append(entry)
    return out


def _find_playwright_chromium_sync() -> str | None:
    """Locate the Chromium binary Playwright already downloaded.

    nodriver drives Chrome/Chromium directly and needs a real executable
    path; reusing Playwright's copy avoids a second browser download in the
    Render build (``playwright install chromium`` already fetches one).

    Uses Playwright's *synchronous* API, which starts a Node.js driver
    subprocess and blocks on real I/O while it does — callers on the async
    event loop must run this via ``asyncio.to_thread``, never call it
    directly, or the whole loop (FastAPI requests, scheduler jobs) stalls
    for the duration.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
            return path if path else None
    except Exception:
        logger.warning("Could not locate Playwright's Chromium for nodriver", exc_info=True)
        return None


async def warm_with_nodriver(url: str, wait_seconds: float = 8.0) -> list[CookieDict]:
    """Visit ``url`` with nodriver until any challenge clears; return cookies.

    Raises on failure to launch/navigate — callers should treat that as a
    failed bypass attempt (:meth:`BotDetectionTracker.record_failure`), same
    as any other tool in the ladder.
    """
    import nodriver  # type: ignore[import-untyped]

    executable_path = await asyncio.to_thread(_find_playwright_chromium_sync)
    browser = await nodriver.start(
        headless=True,
        sandbox=False,
        browser_executable_path=executable_path,
    )
    try:
        await browser.get(url)
        await asyncio.sleep(wait_seconds)
        raw_cookies = await browser.cookies.get_all()
        return _nodriver_cookies_to_playwright(raw_cookies)
    finally:
        browser.stop()


async def warm_with_camoufox(url: str, wait_seconds: float = 8.0) -> list[CookieDict]:
    """Visit ``url`` with Camoufox (patched Firefox) until any challenge
    clears; return cookies in Playwright's ``add_cookies`` shape.

    Camoufox exposes a genuine Playwright ``Browser``, so its own
    ``context.cookies()`` is used directly rather than a format conversion.
    """
    from camoufox.async_api import AsyncCamoufox

    async with AsyncCamoufox(headless=True) as browser:
        context = await browser.new_context()  # type: ignore[union-attr]
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(wait_seconds)
            return cast(list[CookieDict], await context.cookies())
        finally:
            await context.close()


async def warm_cookies(tool: StealthTool, url: str) -> list[CookieDict]:
    """Dispatch to the cookie warmer for ``tool``.

    Raises :class:`ValueError` for a tool with no warmer (PLAYWRIGHT_STEALTH
    patches an existing context rather than warming a new one; PAID_CAPTCHA_
    SOLVER and GIVE_UP aren't warmers at all).
    """
    if tool is StealthTool.NODRIVER:
        return await warm_with_nodriver(url)
    if tool is StealthTool.CAMOUFOX:
        return await warm_with_camoufox(url)
    raise ValueError(f"{tool.value} has no cookie warmer")


# ---------------------------------------------------------------------------
# 2Captcha paid solving (final rung before GIVE_UP)
# ---------------------------------------------------------------------------

class CaptchaSolveError(Exception):
    """Raised when a 2Captcha solve request fails or times out."""


async def solve_recaptcha_v2(sitekey: str, url: str, *, api_key: str | None = None) -> str:
    """Solve an inline reCAPTCHA v2 challenge via 2Captcha; return the token.

    The 2captcha-python client is synchronous (it polls over HTTP), so it
    runs in a worker thread rather than blocking the event loop.
    """
    key = api_key or twocaptcha_api_key()
    if not key:
        raise CaptchaSolveError("TWOCAPTCHA_API_KEY not configured")

    from twocaptcha import (  # type: ignore[import-untyped]
        ApiException,
        NetworkException,
        TimeoutException,
        TwoCaptcha,
    )

    solver = TwoCaptcha(key)
    try:
        result = await asyncio.to_thread(solver.recaptcha, sitekey=sitekey, url=url)
        return result["code"]
    except (ApiException, NetworkException, TimeoutException) as e:
        raise CaptchaSolveError(f"2Captcha reCAPTCHA solve failed: {e}") from e


async def solve_hcaptcha(sitekey: str, url: str, *, api_key: str | None = None) -> str:
    """Solve an inline hCaptcha challenge via 2Captcha; return the token."""
    key = api_key or twocaptcha_api_key()
    if not key:
        raise CaptchaSolveError("TWOCAPTCHA_API_KEY not configured")

    from twocaptcha import (  # type: ignore[import-untyped]
        ApiException,
        NetworkException,
        TimeoutException,
        TwoCaptcha,
    )

    solver = TwoCaptcha(key)
    try:
        result = await asyncio.to_thread(solver.hcaptcha, sitekey=sitekey, url=url)
        return result["code"]
    except (ApiException, NetworkException, TimeoutException) as e:
        raise CaptchaSolveError(f"2Captcha hCaptcha solve failed: {e}") from e


# ---------------------------------------------------------------------------
# Behavioral simulation
# ---------------------------------------------------------------------------

class _KeyboardLike(Protocol):
    async def type(self, text: str) -> None: ...


class _PageLike(Protocol):
    keyboard: _KeyboardLike

    async def click(self, selector: str) -> None: ...


async def human_delay(min_ms: int = 300, max_ms: int = 2500) -> None:
    """Sleep a jittered, human-scale duration. Fixed sleeps are detectable."""
    if min_ms < 0 or max_ms < min_ms:
        raise ValueError("require 0 <= min_ms <= max_ms")
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def human_type(
    page: _PageLike,
    selector: str,
    text: str,
    min_keystroke_ms: int = 50,
    max_keystroke_ms: int = 200,
) -> None:
    """Click a field, then type ``text`` one character at a time with jitter.

    Typing an entire string in one call is a well-known automation tell;
    per-keystroke timing variance is not.
    """
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(min_keystroke_ms, max_keystroke_ms) / 1000)


# ---------------------------------------------------------------------------
# Permanent block tracking (§19 "mark platform as blocked")
# ---------------------------------------------------------------------------

class PlatformBlockError(Exception):
    """Raised when a task pipeline attempts to use a permanently blocked platform."""


class BotDetectionTracker:
    """Tracks per-platform bypass attempts and permanent blocks.

    Backed by any object exposing the ``is_platform_blocked`` /
    ``mark_platform_blocked`` methods (``PersistenceStore`` implements
    these) so a block survives restarts and reincarnation — the same "never
    waste debt-time on a dead end" contract as the Soul Crystal blacklist in
    §19/§20.
    """

    #: Attempts allowed before giving up permanently, independent of the
    #: per-vendor ladder length (artifact.md §19: "If blocked after 3
    #: attempts -> mark platform blocked").
    MAX_ATTEMPTS = 3

    def __init__(self, store: Any) -> None:
        self._store = store
        self._attempts: dict[str, int] = {}

    def is_blocked(self, platform: str) -> bool:
        return bool(self._store.is_platform_blocked(platform))

    def next_tool(self, platform: str, vendor: BotVendor) -> StealthTool:
        """Return the next tool to try for ``platform``, or GIVE_UP.

        Raises :class:`PlatformBlockError` if the platform is already
        permanently blocked — callers should check :meth:`is_blocked` first
        in a hot path, but this guards against a stale in-process check.
        """
        if self.is_blocked(platform):
            raise PlatformBlockError(f"{platform} is permanently blocked")
        attempts = self._attempts.get(platform, 0)
        tool = recommend_tool(vendor, attempts)
        return tool

    def record_failure(self, platform: str, vendor: BotVendor, reason: str = "") -> StealthTool:
        """Record a failed bypass attempt; escalate or permanently block.

        Returns the tool to try next, or :attr:`StealthTool.GIVE_UP` if the
        platform has just been permanently blocked (also persisted).
        """
        attempts = self._attempts.get(platform, 0) + 1
        self._attempts[platform] = attempts

        ladder_exhausted = recommend_tool(vendor, attempts) is StealthTool.GIVE_UP
        if attempts >= self.MAX_ATTEMPTS or ladder_exhausted:
            self._store.mark_platform_blocked(
                platform,
                {"vendor": vendor.value, "attempts": attempts, "reason": reason},
            )
            logger.warning(
                "Platform %r permanently blocked after %d attempts (vendor=%s): %s",
                platform, attempts, vendor.value, reason,
            )
            return StealthTool.GIVE_UP

        return recommend_tool(vendor, attempts)

    def record_success(self, platform: str) -> None:
        """Reset the failure counter after a successful bypass."""
        self._attempts.pop(platform, None)
