"""Supabase-backed persistence layer (Layer 1 "hot memory" per §10).

Falls back to an in-memory store when SUPABASE_URL / SUPABASE_KEY env vars
are absent, so tests and local runs never require real credentials.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Optional

from src.debt_engine import DebtState, DifficultyMode
from src.soul_crystal import LifeRecord, SoulCrystal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _debt_state_to_dict(s: DebtState) -> dict[str, Any]:
    return {
        "debt": str(s.debt),
        "mode": s.mode.value,
        "alive": s.alive,
        "life_number": s.life_number,
        "born_at": s.born_at.isoformat(),
        "last_tick_at": s.last_tick_at.isoformat() if s.last_tick_at else None,
    }


def _debt_state_from_dict(d: dict[str, Any]) -> DebtState:
    return DebtState(
        debt=Decimal(d["debt"]),
        mode=DifficultyMode(d["mode"]),
        alive=d["alive"],
        life_number=d["life_number"],
        born_at=d["born_at"],
        last_tick_at=d.get("last_tick_at"),
    )


def _wallet_to_dict(wallet: Any) -> dict[str, str]:
    return {
        "locked": str(wallet.locked),
        "free": str(wallet.free),
        "debt": str(wallet.debt),
    }


def _wallet_from_dict(d: dict[str, Any], wallet_cls: Any) -> Any:
    return wallet_cls(
        locked=Decimal(d["locked"]),
        free=Decimal(d["free"]),
        debt=Decimal(d["debt"]),
    )


def _life_record_to_dict(r: LifeRecord) -> dict[str, Any]:
    return {
        "life_number": r.life_number,
        "born_at": r.born_at.isoformat(),
        "total_earned": str(r.total_earned),
        "peak_state": r.peak_state,
        "events": r.events,
        "failed_strategies": r.failed_strategies,
        "avoid": r.avoid,
        "best_platform": r.best_platform,
        "best_daily_avg": str(r.best_daily_avg),
    }


def _life_record_from_dict(d: dict[str, Any]) -> LifeRecord:
    return LifeRecord(
        life_number=d["life_number"],
        born_at=d["born_at"],
        total_earned=Decimal(d["total_earned"]),
        peak_state=d["peak_state"],
        events=d.get("events", []),
        failed_strategies=d.get("failed_strategies", []),
        avoid=d.get("avoid", []),
        best_platform=d.get("best_platform", ""),
        best_daily_avg=Decimal(d.get("best_daily_avg", "0")),
    )


def _soul_crystal_to_dict(c: SoulCrystal) -> dict[str, Any]:
    return {
        "life": c.life,
        "born": c.born.isoformat(),
        "died": c.died.isoformat(),
        "lifespan_days": c.lifespan_days,
        "total_earned": str(c.total_earned),
        "peak_state": c.peak_state,
        "best_platform": c.best_platform,
        "best_daily_avg": str(c.best_daily_avg),
        "failed_strategies": c.failed_strategies,
        "avoid": c.avoid,
        "key_lessons": c.key_lessons,
        "cause_of_death": c.cause_of_death,
    }


def _soul_crystal_from_dict(d: dict[str, Any]) -> SoulCrystal:
    return SoulCrystal(
        life=d["life"],
        born=d["born"],
        died=d["died"],
        lifespan_days=d["lifespan_days"],
        total_earned=Decimal(d["total_earned"]),
        peak_state=d.get("peak_state", "thriving"),
        best_platform=d.get("best_platform", ""),
        best_daily_avg=Decimal(d.get("best_daily_avg", "0")),
        failed_strategies=d.get("failed_strategies", []),
        avoid=d.get("avoid", []),
        key_lessons=d.get("key_lessons", []),
        cause_of_death=d.get("cause_of_death", ""),
    )


# ---------------------------------------------------------------------------
# Abstract store
# ---------------------------------------------------------------------------

class PersistenceStore(ABC):
    """Abstract interface for hot-memory persistence."""

    @abstractmethod
    def save_debt_state(self, state: DebtState) -> None: ...

    @abstractmethod
    def load_debt_state(self) -> Optional[DebtState]: ...

    @abstractmethod
    def save_wallet(self, wallet: Any) -> None: ...

    @abstractmethod
    def load_wallet(self) -> Optional[dict[str, str]]: ...

    @abstractmethod
    def save_life_record(self, record: LifeRecord) -> None: ...

    @abstractmethod
    def load_life_record(self) -> Optional[LifeRecord]: ...

    @abstractmethod
    def save_soul_crystal(self, crystal: SoulCrystal) -> None: ...

    @abstractmethod
    def load_soul_crystals(self) -> list[SoulCrystal]: ...

    @abstractmethod
    def save_events(self, events: list[str]) -> None: ...

    @abstractmethod
    def load_events(self) -> list[str]: ...

    @abstractmethod
    def clear(self) -> None:
        """Reset hot-memory state while preserving the soul-crystal archive."""

    @abstractmethod
    def is_payment_processed(self, payment_id: str) -> bool:
        """Return True if this Payoneer payment_id has already been credited.

        Processed-payment records are permanent (§20 payment audit trail) and
        must survive :meth:`clear` / reincarnation — a payout received in a
        past life must never be double-credited or re-processed after death.
        """

    @abstractmethod
    def mark_payment_processed(self, payment_id: str, data: dict[str, Any]) -> None:
        """Record a Payoneer payment_id as processed, with its raw event data."""

    @abstractmethod
    def is_platform_blocked(self, platform: str) -> bool:
        """Return True if this platform was permanently blocked (§19).

        Permanent, like :meth:`is_payment_processed` — a platform blocked in
        a past life stays blocked; the agent must not rediscover the same
        dead end and waste debt-time on it again.
        """

    @abstractmethod
    def mark_platform_blocked(self, platform: str, data: dict[str, Any]) -> None:
        """Permanently record ``platform`` as blocked, with bypass attempt data."""

    @abstractmethod
    def is_platform_scammed(self, platform: str) -> bool:
        """Return True if this platform confirmed-scammed the agent (§20).

        Permanent, like :meth:`is_platform_blocked` — a platform that scammed
        a past life is never rejoined; the lesson also lives in the Soul
        Crystal, but this check must be cheap and available before research.
        """

    @abstractmethod
    def mark_platform_scammed(self, platform: str, data: dict[str, Any]) -> None:
        """Permanently record ``platform`` as a confirmed scam, with evidence."""


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------

class InMemoryStore(PersistenceStore):
    """Dict-backed store for local dev and testing."""

    def __init__(self) -> None:
        self._debt_state: dict[str, Any] | None = None
        self._wallet: dict[str, str] | None = None
        self._life_record: dict[str, Any] | None = None
        self._soul_crystals: list[dict[str, Any]] = []
        self._events: list[str] = []
        self._processed_payments: dict[str, dict[str, Any]] = {}
        self._blocked_platforms: dict[str, dict[str, Any]] = {}
        self._scammed_platforms: dict[str, dict[str, Any]] = {}

    def save_debt_state(self, state: DebtState) -> None:
        self._debt_state = _debt_state_to_dict(state)

    def load_debt_state(self) -> Optional[DebtState]:
        if self._debt_state is None:
            return None
        return _debt_state_from_dict(self._debt_state)

    def save_wallet(self, wallet: Any) -> None:
        self._wallet = _wallet_to_dict(wallet)

    def load_wallet(self) -> Optional[dict[str, str]]:
        return self._wallet

    def save_life_record(self, record: LifeRecord) -> None:
        self._life_record = _life_record_to_dict(record)

    def load_life_record(self) -> Optional[LifeRecord]:
        if self._life_record is None:
            return None
        return _life_record_from_dict(self._life_record)

    def save_soul_crystal(self, crystal: SoulCrystal) -> None:
        self._soul_crystals.append(_soul_crystal_to_dict(crystal))

    def load_soul_crystals(self) -> list[SoulCrystal]:
        return [_soul_crystal_from_dict(c) for c in self._soul_crystals]

    def save_events(self, events: list[str]) -> None:
        self._events = list(events)

    def load_events(self) -> list[str]:
        return list(self._events)

    def clear(self) -> None:
        # Preserve the permanent soul-crystal archive (§10 Layer 2/3): it must
        # survive reincarnation. Only wipe the hot-memory state (wallet, task
        # queue, events, life record) that belongs to the dying life.
        # processed_payments, blocked_platforms and scammed_platforms are also
        # permanent (§19/§20) and are intentionally not cleared here.
        self._debt_state = None
        self._wallet = None
        self._life_record = None
        self._events.clear()

    def is_payment_processed(self, payment_id: str) -> bool:
        return payment_id in self._processed_payments

    def mark_payment_processed(self, payment_id: str, data: dict[str, Any]) -> None:
        self._processed_payments[payment_id] = dict(data)

    def is_platform_blocked(self, platform: str) -> bool:
        return platform in self._blocked_platforms

    def mark_platform_blocked(self, platform: str, data: dict[str, Any]) -> None:
        self._blocked_platforms[platform] = dict(data)

    def is_platform_scammed(self, platform: str) -> bool:
        return platform in self._scammed_platforms

    def mark_platform_scammed(self, platform: str, data: dict[str, Any]) -> None:
        self._scammed_platforms[platform] = dict(data)


# ---------------------------------------------------------------------------
# Supabase-backed store
# ---------------------------------------------------------------------------

class SupabaseStore(PersistenceStore):
    """Supabase-backed hot-memory store.

    Each logical entity maps to a Supabase table.  Tables are created
    automatically on first use (via ``upsert``) so no manual migration is
    required for the hot-memory layer.
    """

    # Single-row tables (keyed by a fixed ``id`` column)
    _ROW_TABLES = ("debt_state", "wallet", "life_record")
    # Append-only list (one row per event / crystal)
    _LIST_TABLES = ("events", "soul_crystals")

    def __init__(self) -> None:
        from supabase import create_client

        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self._client = create_client(url, key)
        self._ensure_tables()

    # -- bootstrap ----------------------------------------------------------

    def _ensure_tables(self) -> None:
        """Create hot-memory tables if they don't already exist.

        Uses Supabase's RPC to run DDL.  If the tables already exist this
        is a harmless no-op (the ``IF NOT EXISTS`` guard prevents errors).
        """
        ddl = """
        CREATE TABLE IF NOT EXISTS debt_state (
            id    TEXT PRIMARY KEY DEFAULT 'current',
            data  JSONB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wallet (
            id    TEXT PRIMARY KEY DEFAULT 'current',
            data  JSONB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS life_record (
            id    TEXT PRIMARY KEY DEFAULT 'current',
            data  JSONB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id    BIGSERIAL PRIMARY KEY,
            data  JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS soul_crystals (
            id    BIGSERIAL PRIMARY KEY,
            data  JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS processed_payments (
            id    TEXT PRIMARY KEY,
            data  JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS blocked_platforms (
            id    TEXT PRIMARY KEY,
            data  JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS scammed_platforms (
            id    TEXT PRIMARY KEY,
            data  JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        """
        try:
            self._client.rpc("exec_sql", {"query": ddl}).execute()
        except Exception:
            # Tables likely already exist or RPC not configured — proceed
            logger.debug("Table bootstrap skipped (RPC unavailable)")

    # -- helpers ------------------------------------------------------------

    def _upsert_row(self, table: str, row_id: str, data: dict) -> None:
        payload = {"id": row_id, "data": data}
        self._client.table(table).upsert(payload, on_conflict="id").execute()

    def _load_row(self, table: str, row_id: str) -> Optional[dict]:
        resp = (
            self._client.table(table)
            .select("data")
            .eq("id", row_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0]["data"] if rows else None

    def _append(self, table: str, data: dict) -> None:
        self._client.table(table).insert({"data": data}).execute()

    def _load_all(self, table: str) -> list[dict]:
        resp = self._client.table(table).select("data").order("id").execute()
        return [r["data"] for r in (resp.data or [])]

    def _delete_all(self, table: str) -> None:
        self._client.table(table).delete().neq("id", "-1").execute()

    # -- debt_state ---------------------------------------------------------

    def save_debt_state(self, state: DebtState) -> None:
        self._upsert_row("debt_state", "current", _debt_state_to_dict(state))

    def load_debt_state(self) -> Optional[DebtState]:
        d = self._load_row("debt_state", "current")
        return _debt_state_from_dict(d) if d else None

    # -- wallet -------------------------------------------------------------

    def save_wallet(self, wallet: Any) -> None:
        self._upsert_row("wallet", "current", _wallet_to_dict(wallet))

    def load_wallet(self) -> Optional[dict[str, str]]:
        return self._load_row("wallet", "current")

    # -- life_record --------------------------------------------------------

    def save_life_record(self, record: LifeRecord) -> None:
        self._upsert_row("life_record", "current", _life_record_to_dict(record))

    def load_life_record(self) -> Optional[LifeRecord]:
        d = self._load_row("life_record", "current")
        return _life_record_from_dict(d) if d else None

    # -- soul_crystals ------------------------------------------------------

    def save_soul_crystal(self, crystal: SoulCrystal) -> None:
        self._append("soul_crystals", _soul_crystal_to_dict(crystal))

    def load_soul_crystals(self) -> list[SoulCrystal]:
        return [_soul_crystal_from_dict(d) for d in self._load_all("soul_crystals")]

    # -- events -------------------------------------------------------------

    def save_events(self, events: list[str]) -> None:
        # Replace, not append: _persist_all() calls save_events with the full
        # event log on every tick, so appending would duplicate rows quadratically.
        self._delete_all("events")
        for ev in events:
            self._append("events", {"text": ev})

    def load_events(self) -> list[str]:
        rows = self._load_all("events")
        return [r.get("text", "") for r in rows]

    # -- lifecycle ----------------------------------------------------------

    def clear(self) -> None:
        """Reset hot-memory state (called on death / reincarnation).

        Preserves the permanent soul-crystal archive — soul crystals are
        §10 Layer 2/3 permanent memory and must survive the wipe. Only the
        dying life's hot-memory tables (debt_state, wallet, life_record,
        events) are reset.
        """
        for table in self._ROW_TABLES:
            self._delete_all(table)
        self._delete_all("events")

    # -- processed_payments (§20 payment audit trail — permanent) -----------

    def is_payment_processed(self, payment_id: str) -> bool:
        return self._load_row("processed_payments", payment_id) is not None

    def mark_payment_processed(self, payment_id: str, data: dict[str, Any]) -> None:
        self._upsert_row("processed_payments", payment_id, data)

    # -- blocked_platforms (§19 permanent bot-block memory) -----------------

    def is_platform_blocked(self, platform: str) -> bool:
        return self._load_row("blocked_platforms", platform) is not None

    def mark_platform_blocked(self, platform: str, data: dict[str, Any]) -> None:
        self._upsert_row("blocked_platforms", platform, data)

    # -- scammed_platforms (§20 permanent scam memory) -----------------------

    def is_platform_scammed(self, platform: str) -> bool:
        return self._load_row("scammed_platforms", platform) is not None

    def mark_platform_scammed(self, platform: str, data: dict[str, Any]) -> None:
        self._upsert_row("scammed_platforms", platform, data)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_persistence_store() -> PersistenceStore:
    """Return a SupabaseStore when credentials exist, else InMemoryStore."""
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
        logger.info("Using Supabase-backed persistence")
        return SupabaseStore()
    logger.info("No SUPABASE_URL/KEY — using in-memory persistence")
    return InMemoryStore()
