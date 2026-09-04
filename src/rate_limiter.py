"""Rate-Limit Tracker — proactive per-provider usage tracking (artifact.md §13 item).

Records per-provider call counts across two sliding windows (per-minute and
per-day) and proactively *skips* a provider whose window usage exceeds a
configurable safety margin, rather than waiting for a reactive HTTP 429/error
from the provider.

Two backends:

* **InMemoryRateLimitStore** — dict-backed; resets on restart.  Fine for
  single-process local dev.
* **SupabaseRateLimitStore** — persisted to a ``rate_limit_usage`` table so
  counts survive Render sleep/restart and work across cron invocations.

The **RateLimitTracker** facade wraps a store and exposes the
``is_available`` / ``record_usage`` pair used by ``brain_router.py``.
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-provider limit definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderLimits:
    """Rate-limit budget for a single provider."""
    rpm: int          # requests per minute
    rpd: int | None = None  # requests per day (None = unlimited)
    tpd: int | None = None  # tokens per day (informational, not enforced)


# Canonical limits — matches PROVIDER_CONFIGS in brain_router.py
PROVIDER_LIMITS: dict[str, ProviderLimits] = {
    "nvidia_nim":   ProviderLimits(rpm=40,  rpd=None,  tpd=None),
    "groq":         ProviderLimits(rpm=30,  rpd=14400, tpd=None),
    "gemini_flash": ProviderLimits(rpm=1500, rpd=1500,  tpd=1_000_000),
    "cerebras":     ProviderLimits(rpm=10,  rpd=None,  tpd=None),
    "mistral":      ProviderLimits(rpm=2,   rpd=None,  tpd=1_000_000),
    "openrouter":   ProviderLimits(rpm=50,  rpd=None,  tpd=None),
    "cloudflare":   ProviderLimits(rpm=10000, rpd=None, tpd=None),
    "freellmapi":   ProviderLimits(rpm=100, rpd=None,  tpd=None),
}


# ---------------------------------------------------------------------------
# Abstract store
# ---------------------------------------------------------------------------

class RateLimitStore(ABC):
    """Interface for persisting rate-limit usage counters."""

    @abstractmethod
    def increment(self, provider: str, window: str) -> int:
        """Increment counter for *provider* in *window*.  Return new count."""
        ...

    @abstractmethod
    def get_count(self, provider: str, window: str) -> int:
        """Return current counter for *provider* in *window*."""
        ...

    @abstractmethod
    def reset_window(self, provider: str, window: str) -> None:
        """Reset a single window counter to zero."""
        ...

    @abstractmethod
    def save_state(self, states: dict[str, dict[str, int]]) -> None:
        """Bulk-save all provider window counters (checkpoint)."""
        ...

    @abstractmethod
    def load_state(self) -> dict[str, dict[str, int]]:
        """Load all provider window counters (restore on startup)."""
        ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

class InMemoryRateLimitStore(RateLimitStore):
    """Dict-backed counters — resets on process restart."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str], int] = {}

    def increment(self, provider: str, window: str) -> int:
        key = (provider, window)
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    def get_count(self, provider: str, window: str) -> int:
        return self._counters.get((provider, window), 0)

    def reset_window(self, provider: str, window: str) -> None:
        self._counters[(provider, window)] = 0

    def save_state(self, states: dict[str, dict[str, int]]) -> None:
        for provider, windows in states.items():
            for window, count in windows.items():
                self._counters[(provider, window)] = count

    def load_state(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for (provider, window), count in self._counters.items():
            result.setdefault(provider, {})[window] = count
        return result


# ---------------------------------------------------------------------------
# Supabase store
# ---------------------------------------------------------------------------

class SupabaseRateLimitStore(RateLimitStore):
    """Supabase-backed counters — survives Render restarts.

    Table::

        CREATE TABLE IF NOT EXISTS rate_limit_usage (
            provider TEXT NOT NULL,
            window   TEXT NOT NULL,
            count    INT NOT NULL DEFAULT 0,
            updated  TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (provider, window)
        );
    """

    def __init__(self) -> None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self._client = create_client(url, key)
        self._ensure_table()

    def _ensure_table(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS rate_limit_usage (
            provider TEXT NOT NULL,
            window   TEXT NOT NULL,
            count    INT NOT NULL DEFAULT 0,
            updated  TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (provider, window)
        );
        """
        try:
            self._client.rpc("exec_sql", {"query": ddl}).execute()
        except Exception:  # noqa: BLE001 - intentional graceful fallback
            logger.debug("rate_limit_usage table bootstrap skipped (RPC unavailable)")

    def increment(self, provider: str, window: str) -> int:
        # Read current
        resp = (
            self._client.table("rate_limit_usage")
            .select("count")
            .eq("provider", provider)
            .eq("window", window)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        current = rows[0]["count"] if rows else 0
        new_count = current + 1
        # Upsert
        self._client.table("rate_limit_usage").upsert(
            {"provider": provider, "window": window, "count": new_count},
            on_conflict="provider,window",
        ).execute()
        return new_count

    def get_count(self, provider: str, window: str) -> int:
        resp = (
            self._client.table("rate_limit_usage")
            .select("count")
            .eq("provider", provider)
            .eq("window", window)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0]["count"] if rows else 0

    def reset_window(self, provider: str, window: str) -> None:
        self._client.table("rate_limit_usage").upsert(
            {"provider": provider, "window": window, "count": 0},
            on_conflict="provider,window",
        ).execute()

    def save_state(self, states: dict[str, dict[str, int]]) -> None:
        for provider, windows in states.items():
            for window, count in windows.items():
                self._client.table("rate_limit_usage").upsert(
                    {"provider": provider, "window": window, "count": count},
                    on_conflict="provider,window",
                ).execute()

    def load_state(self) -> dict[str, dict[str, int]]:
        resp = (
            self._client.table("rate_limit_usage")
            .select("provider,window,count")
            .execute()
        )
        result: dict[str, dict[str, int]] = {}
        for row in (resp.data or []):
            result.setdefault(row["provider"], {})[row["window"]] = row["count"]
        return result


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class RateLimitTracker:
    """Proactive rate-limit tracker used by BrainRouter.

    For each provider the tracker maintains two sliding-window counters:

    * ``minute`` — rolling 60-second window (for RPM limits).
    * ``day`` — rolling 24-hour window (for RPD limits).

    A provider is considered **unavailable** when any active counter exceeds
    ``safety_margin`` (default 85 %) of the provider's budget.  This lets
    the router skip it *before* it actually hits 429.

    Windows reset automatically when the elapsed time exceeds the window
    duration (tracked via ``_window_start`` timestamps).
    """

    def __init__(
        self,
        store: RateLimitStore | None = None,
        *,
        safety_margin: float = 0.85,
    ) -> None:
        self._store: RateLimitStore = store or InMemoryRateLimitStore()
        self.safety_margin = safety_margin
        # Track wall-clock start of each window per provider
        self._minute_start: dict[str, float] = {}
        self._day_start: dict[str, float] = {}

    # -- window management ----------------------------------------------------

    def _now(self) -> float:
        return time.time()

    def _maybe_reset_minute(self, provider: str) -> None:
        now = self._now()
        start = self._minute_start.get(provider, now)
        if now - start >= 60:
            self._store.reset_window(provider, "minute")
            self._minute_start[provider] = now

    def _maybe_reset_day(self, provider: str) -> None:
        now = self._now()
        start = self._day_start.get(provider, now)
        if now - start >= 86400:
            self._store.reset_window(provider, "day")
            self._day_start[provider] = now

    # -- public API -----------------------------------------------------------

    def record_usage(self, provider: str) -> None:
        """Record one request for *provider* in both windows."""
        self._maybe_reset_minute(provider)
        self._maybe_reset_day(provider)
        self._store.increment(provider, "minute")
        self._store.increment(provider, "day")

    def is_available(self, provider: str) -> bool:
        """Return ``True`` if the provider is within its safe budget."""
        limits = PROVIDER_LIMITS.get(provider)
        if limits is None:
            # Unknown provider — allow by default
            return True

        self._maybe_reset_minute(provider)
        self._maybe_reset_day(provider)

        # Check RPM
        minute_count = self._store.get_count(provider, "minute")
        rpm_budget = int(limits.rpm * self.safety_margin)
        if minute_count >= rpm_budget:
            logger.debug(
                f"{provider} RPM limit approaching: "
                f"{minute_count}/{limits.rpm} (safe: {rpm_budget})"
            )
            return False

        # Check RPD
        if limits.rpd is not None:
            day_count = self._store.get_count(provider, "day")
            rpd_budget = int(limits.rpd * self.safety_margin)
            if day_count >= rpd_budget:
                logger.debug(
                    f"{provider} RPD limit approaching: "
                    f"{day_count}/{limits.rpd} (safe: {rpd_budget})"
                )
                return False

        return True

    def get_usage(self, provider: str) -> dict[str, int]:
        """Return current usage counts for a provider."""
        self._maybe_reset_minute(provider)
        self._maybe_reset_day(provider)
        return {
            "minute": self._store.get_count(provider, "minute"),
            "day": self._store.get_count(provider, "day"),
        }

    def get_limits(self, provider: str) -> ProviderLimits | None:
        """Return the configured limits for a provider."""
        return PROVIDER_LIMITS.get(provider)

    def get_status(self) -> dict[str, dict]:
        """Return full status for all known providers (useful for dashboard)."""
        result: dict[str, dict] = {}
        for name, limits in PROVIDER_LIMITS.items():
            usage = self.get_usage(name)
            available = self.is_available(name)
            result[name] = {
                "rpm": limits.rpm,
                "rpd": limits.rpd,
                "usage_minute": usage["minute"],
                "usage_day": usage["day"],
                "available": available,
            }
        return result

    def checkpoint(self) -> None:
        """Persist current state to store (call periodically)."""
        states: dict[str, dict[str, int]] = {}
        for name in PROVIDER_LIMITS:
            states[name] = {
                "minute": self._store.get_count(name, "minute"),
                "day": self._store.get_count(name, "day"),
            }
        self._store.save_state(states)

    def restore(self) -> None:
        """Restore state from store (call on startup)."""
        saved = self._store.load_state()
        for provider, windows in saved.items():
            for window, count in windows.items():
                # We can't restore exact timestamps, so set start = now
                # and let the window reset naturally if expired
                if window == "minute":
                    self._minute_start[provider] = self._now()
                    self._store.save_state({provider: {window: count}})
                elif window == "day":
                    self._day_start[provider] = self._now()
                    self._store.save_state({provider: {window: count}})

    def reset_provider(self, provider: str) -> None:
        """Reset all counters for a provider."""
        self._store.reset_window(provider, "minute")
        self._store.reset_window(provider, "day")
        self._minute_start.pop(provider, None)
        self._day_start.pop(provider, None)

    def reset_all(self) -> None:
        """Reset all providers."""
        for name in PROVIDER_LIMITS:
            self.reset_provider(name)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_tracker: RateLimitTracker | None = None


def get_rate_limiter() -> RateLimitTracker:
    """Get (or create) the global rate-limiter singleton."""
    global _tracker
    if _tracker is None:
        _tracker = _create_tracker()
    return _tracker


def _create_tracker() -> RateLimitTracker:
    """Create a tracker with Supabase store when available, else in-memory."""
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
        try:
            store = SupabaseRateLimitStore()
            logger.info("Rate limiter: using Supabase store")
            return RateLimitTracker(store)
        except Exception as e:  # noqa: BLE001 - intentional fallback
            logger.warning(f"Rate limiter: Supabase init failed ({e}), falling back to memory")
    else:
        logger.info("Rate limiter: using in-memory store")
    return RateLimitTracker(InMemoryRateLimitStore())


def create_rate_limiter(**kwargs) -> RateLimitTracker:
    """Create a fresh tracker instance (useful in tests)."""
    return RateLimitTracker(**kwargs)
