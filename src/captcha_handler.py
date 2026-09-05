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

What this module deliberately does *not* do: it does not implement nodriver
or Camoufox integration (those are separate browser engines with their own
async APIs, incompatible with the Playwright-based connectors already in
``task_executor.py`` — swapping engines is a larger, separate effort) or a
paid 2captcha fallback. ``playwright-stealth`` *is* wired in
(``task_executor.BrowserSessionManager``) since it patches an existing
Playwright context/page rather than requiring a different engine. Treat the
escalation ladder below as reflecting that: only the free, already-integrated
tool is offered; the harder tools raise :class:`NotImplementedError` markers
in :data:`TOOL_IMPLEMENTED` so callers can detect "designed but not wired"
without guessing.
"""

from __future__ import annotations

import asyncio
import logging
import random
from enum import Enum
from typing import Any, Protocol

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


#: Which tools are actually wired into task_executor.py today. Tools not in
#: this set are part of the §19 design but not yet integrated — callers
#: should treat a recommendation of one of those as "blocked pending
#: implementation", not "try it and see".
TOOL_IMPLEMENTED: dict[StealthTool, bool] = {
    StealthTool.PLAYWRIGHT_STEALTH: True,
    StealthTool.NODRIVER: False,
    StealthTool.CAMOUFOX: False,
    StealthTool.PAID_CAPTCHA_SOLVER: False,
    StealthTool.GIVE_UP: True,
}

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
