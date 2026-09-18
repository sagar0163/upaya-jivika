"""Supabase-backed persistence layer (Layer 1 "hot memory" per §10).

Falls back to an in-memory store when SUPABASE_URL / SUPABASE_KEY env vars
are absent, so tests and local runs never require real credentials.
"""

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.debt_engine import DebtState, DifficultyMode
from src.soul_crystal import LifeRecord, SoulCrystal

logger = logging.getLogger(__name__)

#: Research dedup window — only one full research cycle per 6 h window
#: (issue #71). Mirrors the GH Actions cron cadence (``0 */6 * * *``).
RESEARCH_DEDUP_WINDOW_HOURS = 6


def research_window_id(now: datetime | None = None) -> str:
    """Return the 6-hour research window bucket id for ``now``.

    Two researchers running in the same UTC 6 h bucket share the same id,
    so an atomic claim (see :meth:`PersistenceStore.try_claim_research_window`)
    lets only one of them fire the full cycle. An id is cheap because it is
    derived arithmetically rather than stored.
    """
    now = now or datetime.now(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    hours = int((now - epoch).total_seconds() // 3600)
    window_hour = hours - (hours % RESEARCH_DEDUP_WINDOW_HOURS)
    return f"r{window_hour}"


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
        "escrow": str(getattr(wallet, "escrow", Decimal("0.00"))),
    }


def _wallet_from_dict(d: dict[str, Any], wallet_cls: Any) -> Any:
    # ``escrow`` is optional so legacy snapshots (pre-#76) still restore; the
    # escrow pool simply defaults to zero when it was never persisted.
    return wallet_cls(
        locked=Decimal(d["locked"]),
        free=Decimal(d["free"]),
        debt=Decimal(d["debt"]),
        escrow=Decimal(d.get("escrow", "0.00")),
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
        # Ancestral-memory carry-over (issue #60): top-3 platform certainties
        # and task affinities learned this life, so a reborn life starts with
        # quantitative knowledge of what research deemed most certain.
        "platform_certainties": [
            {"platform": p, "certainty": str(v)} for p, v in c.platform_certainties
        ],
        "task_affinities": [
            {"task_type": t, "affinity": str(v)} for t, v in c.task_affinities
        ],
    }


def _top3_from_dict(raw: Any, key_field: str, value_field: str) -> list[tuple[str, Decimal]]:
    """Decode a persisted top-3 list into ``[(name, Decimal), ...]``.

    Accepts the current ``[{"platform": p, "certainty": s}]`` shape as well as
    the legacy ``[[p, s]]`` pair-list shape, and silently ignores anything
    unparseable so old archives never break reincarnation.
    """
    if not isinstance(raw, list):
        return []
    result: list[tuple[str, Decimal]] = []
    for item in raw:
        try:
            if isinstance(item, dict):
                name = item.get(key_field)
                value = item.get(value_field)
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                name, value = item
            else:
                continue
            if name is None or value is None:
                continue
            result.append((str(name), Decimal(str(value))))
        except (ValueError, TypeError, ArithmeticError):
            continue
    return result


# ---------------------------------------------------------------------------
# Research Score serialisation (for research → execution → feedback loop)
# ---------------------------------------------------------------------------

class ResearchScore(BaseModel):
    """A research result with platform certainty scores."""

    topic: str
    query: str
    findings: list[dict[str, Any]]
    summary: str
    confidence: float
    sources: list[str]
    timestamp: datetime | None = None
    platform_certainties: dict[str, float] = Field(default_factory=dict)  # platform -> certainty
    task_affinities: dict[str, float] = Field(default_factory=dict)  # task_type -> affinity


def _research_score_to_dict(s: ResearchScore) -> dict[str, Any]:
    return {
        "topic": s.topic,
        "query": s.query,
        "findings": s.findings,
        "summary": s.summary,
        "confidence": s.confidence,
        "sources": s.sources,
        "timestamp": s.timestamp.isoformat() if s.timestamp else None,
        "platform_certainties": s.platform_certainties,
        "task_affinities": s.task_affinities,
    }


def _research_score_from_dict(d: dict[str, Any]) -> ResearchScore:
    ts = datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else datetime.now(timezone.utc)
    return ResearchScore(
        topic=d["topic"],
        query=d["query"],
        findings=d["findings"],
        summary=d["summary"],
        confidence=d["confidence"],
        sources=d["sources"],
        timestamp=ts,
        platform_certainties=d.get("platform_certainties", {}),
        task_affinities=d.get("task_affinities", {}),
    )


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
        platform_certainties=_top3_from_dict(
            d.get("platform_certainties", []), "platform", "certainty"
        ),
        task_affinities=_top3_from_dict(
            d.get("task_affinities", []), "task_type", "affinity"
        ),
    )


# ---------------------------------------------------------------------------
# Abstract store
# ---------------------------------------------------------------------------


class PersistenceStore(ABC):
    """Abstract interface for hot-memory persistence."""

    @abstractmethod
    def save_debt_state(self, state: DebtState) -> None: ...

    @abstractmethod
    def save_research_score(self, score: ResearchScore) -> None: ...

    @abstractmethod
    def load_research_scores(self) -> list[ResearchScore]: ...

    @abstractmethod
    def clear_research_scores(self) -> None:
        """Remove the research-score table (issue #63).

        Research scores encode the *current* life's read on platform
        certainty. On reincarnation the dying life's scores are wiped and the
        next life is re-seeded from the soul-crystal carry-over (top-3 only),
        so inherited wisdom is bounded and fresh research still dominates.
        """

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
    def save_survival_mode(self, enabled: bool) -> None:
        """Persist the survival-mode toggle (issue #63).

        The toggle is operator-visible, so it must survive restarts and be
        readable on boot to override the env-var default.
        """

    @abstractmethod
    def load_survival_mode(self) -> Optional[bool]:
        """Return the persisted survival-mode value, or None if never set."""

    @abstractmethod
    def save_last_research_at(self, ts: datetime) -> None:
        """Persist the timestamp of the last completed research cycle.

        Used by both the in-app scheduler and the GH Actions cron script to
        deduplicate: only one runner fires per 6-hour window.
        """

    @abstractmethod
    def load_last_research_at(self) -> Optional[datetime]:
        """Return the timestamp of the last completed research cycle, or None."""

    @abstractmethod
    def try_claim_research_window(self, window_id: str) -> bool:
        """Atomically reserve ``window_id`` for a research cycle.

        Returns True the first time this 6-hour window is claimed, False if
        already claimed (concurrently or earlier). Both the in-app scheduler
        and GH Actions cron call this *before* running the expensive research
        loop so two concurrent triggers cannot both fire a full cycle
        (issue #71)."""

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
    def try_claim_payment(self, payment_id: str) -> bool:
        """Atomically reserve ``payment_id`` for processing.

        Returns True the first time this id is claimed, False if it was
        already claimed (by this call or a concurrent one) — the caller must
        credit the wallet only on True. This exists because
        ``is_payment_processed`` + later ``mark_payment_processed`` is a
        check-then-act race: two concurrent deliveries of the same webhook
        (Payoneer retries, or a duplicating load balancer) can both pass the
        check before either marks it processed, double-crediting the wallet.
        ``try_claim_payment`` closes that window with a single atomic op.
        """

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

    @abstractmethod
    def save_pending_spend(self, spend_id: str, data: dict[str, Any]) -> None:
        """Save/update a pending AI spend awaiting its veto window (§14)."""

    @abstractmethod
    def load_pending_spend(self, spend_id: str) -> Optional[dict[str, Any]]:
        """Load one pending spend by id, or None if it doesn't exist."""

    @abstractmethod
    def load_pending_spends(self) -> list[dict[str, Any]]:
        """Load all pending spends awaiting resolution."""

    @abstractmethod
    def delete_pending_spend(self, spend_id: str) -> None:
        """Remove a pending spend once it's been resolved (approved/rejected)."""

    # -- delegations (§14/§20 — human delegation escrow ledger, life-scoped)-

    @abstractmethod
    def save_commitment(self, commitment_id: str, data: dict[str, Any]) -> None:
        """Save/update a human-delegation commitment (issue #76)."""

    @abstractmethod
    def load_commitments(self) -> list[dict[str, Any]]:
        """Load all human-delegation commitments from the escrow ledger."""


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
        self._survival_mode: Optional[bool] = None
        self._processed_payments: dict[str, dict[str, Any]] = {}
        self._research_scores: list[dict[str, Any]] = []
        self._payment_lock = threading.Lock()
        self._research_lock = threading.Lock()
        self._blocked_platforms: dict[str, dict[str, Any]] = {}
        self._scammed_platforms: dict[str, dict[str, Any]] = {}
        self._pending_spends: dict[str, dict[str, Any]] = {}
        self._commitments: dict[str, dict[str, Any]] = {}
        self._research_windows: set[str] = set()

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

    def save_survival_mode(self, enabled: bool) -> None:
        self._survival_mode = bool(enabled)

    def load_survival_mode(self) -> Optional[bool]:
        return self._survival_mode

    def save_last_research_at(self, ts: datetime) -> None:
        self._last_research_at = ts

    def load_last_research_at(self) -> Optional[datetime]:
        return getattr(self, "_last_research_at", None)

    def try_claim_research_window(self, window_id: str) -> bool:
        with self._research_lock:
            if window_id in self._research_windows:
                return False
            self._research_windows.add(window_id)
            return True

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
        # Pending spends are life-scoped (tied to a wallet that's about to
        # reset) — a dying life's undecided spend decisions don't carry over.
        self._pending_spends.clear()
        # Delegation commitments are life-scoped for the same reason: their
        # escrow lives in the wallet that is being wiped.
        self._commitments.clear()

    def is_payment_processed(self, payment_id: str) -> bool:
        return payment_id in self._processed_payments

    def mark_payment_processed(self, payment_id: str, data: dict[str, Any]) -> None:
        self._processed_payments[payment_id] = dict(data)

    def try_claim_payment(self, payment_id: str) -> bool:
        with self._payment_lock:
            if payment_id in self._processed_payments:
                return False
            self._processed_payments[payment_id] = {}
            return True

    def is_platform_blocked(self, platform: str) -> bool:
        return platform in self._blocked_platforms

    def mark_platform_blocked(self, platform: str, data: dict[str, Any]) -> None:
        self._blocked_platforms[platform] = dict(data)

    def is_platform_scammed(self, platform: str) -> bool:
        return platform in self._scammed_platforms

    def mark_platform_scammed(self, platform: str, data: dict[str, Any]) -> None:
        self._scammed_platforms[platform] = dict(data)

    def save_pending_spend(self, spend_id: str, data: dict[str, Any]) -> None:
        self._pending_spends[spend_id] = dict(data)

    def load_pending_spend(self, spend_id: str) -> Optional[dict[str, Any]]:
        return self._pending_spends.get(spend_id)

    def load_pending_spends(self) -> list[dict[str, Any]]:
        return list(self._pending_spends.values())

    def delete_pending_spend(self, spend_id: str) -> None:
        self._pending_spends.pop(spend_id, None)

    # -- delegations (issue #76: human-delegation escrow ledger) ------------

    def save_commitment(self, commitment_id: str, data: dict[str, Any]) -> None:
        self._commitments[commitment_id] = dict(data)

    def load_commitments(self) -> list[dict[str, Any]]:
        return list(self._commitments.values())

    # -- research_scores (issue #60: research → execution → feedback) ---------

    def save_research_score(self, score: ResearchScore) -> None:
        """Save a research score to the store."""
        self._research_scores.append(_research_score_to_dict(score))

    def load_research_scores(self) -> list[ResearchScore]:
        """Load all research scores from the store."""
        return [_research_score_from_dict(d) for d in self._research_scores]

    def clear_research_scores(self) -> None:
        """Remove all research scores from the store."""
        self._research_scores.clear()


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
        CREATE TABLE IF NOT EXISTS pending_spends (
            id    TEXT PRIMARY KEY,
            data  JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS commitments (
            id    TEXT PRIMARY KEY,
            data  JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            id    TEXT PRIMARY KEY,
            data  JSONB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS research_scores (
            id    BIGSERIAL PRIMARY KEY,
            data  JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS research_windows (
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

    # -- research_scores ----------------------------------------------------

    def save_research_score(self, score: ResearchScore) -> None:
        self._append("research_scores", _research_score_to_dict(score))

    def load_research_scores(self) -> list[ResearchScore]:
        return [_research_score_from_dict(d) for d in self._load_all("research_scores")]

    def clear_research_scores(self) -> None:
        self._delete_all("research_scores")

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

    # -- app_settings -------------------------------------------------------

    def save_survival_mode(self, enabled: bool) -> None:
        self._upsert_row("app_settings", "survival_mode", {"enabled": bool(enabled)})

    def load_survival_mode(self) -> Optional[bool]:
        d = self._load_row("app_settings", "survival_mode")
        if d is None:
            return None
        try:
            return bool(d["enabled"])
        except (KeyError, TypeError, ValueError):
            return None

    def save_last_research_at(self, ts: datetime) -> None:
        self._upsert_row("app_settings", "last_research_at", {"ts": ts.isoformat()})

    def load_last_research_at(self) -> Optional[datetime]:
        d = self._load_row("app_settings", "last_research_at")
        if d is None:
            return None
        try:
            return datetime.fromisoformat(d["ts"])
        except (KeyError, TypeError, ValueError):
            return None

    def try_claim_research_window(self, window_id: str) -> bool:
        try:
            self._client.table("research_windows").insert(
                {"id": window_id, "data": {}}
            ).execute()
            return True
        except Exception as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                return False
            raise

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
        # Pending spends are life-scoped (tied to a wallet that's about to
        # reset) — a dying life's undecided spend decisions don't carry over.
        self._delete_all("pending_spends")
        # Delegation commitments are life-scoped for the same reason: their
        # escrow lives in the wallet that is being wiped (issue #76).
        self._delete_all("commitments")

    # -- processed_payments (§20 payment audit trail — permanent) -----------

    def is_payment_processed(self, payment_id: str) -> bool:
        return self._load_row("processed_payments", payment_id) is not None

    def mark_payment_processed(self, payment_id: str, data: dict[str, Any]) -> None:
        self._upsert_row("processed_payments", payment_id, data)

    def try_claim_payment(self, payment_id: str) -> bool:
        # A plain INSERT (not upsert) is the atomic op here: the "id" column
        # is the primary key, so a concurrent duplicate insert fails with a
        # unique-violation from Postgres itself rather than racing on a
        # separate read-then-write round trip in this process.
        try:
            self._client.table("processed_payments").insert(
                {"id": payment_id, "data": {}}
            ).execute()
            return True
        except Exception as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                return False
            raise

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

    # -- pending_spends (§14 human approval gate — life-scoped) --------------

    def save_pending_spend(self, spend_id: str, data: dict[str, Any]) -> None:
        self._upsert_row("pending_spends", spend_id, data)

    def load_pending_spend(self, spend_id: str) -> Optional[dict[str, Any]]:
        return self._load_row("pending_spends", spend_id)

    def load_pending_spends(self) -> list[dict[str, Any]]:
        return self._load_all("pending_spends")

    def delete_pending_spend(self, spend_id: str) -> None:
        self._client.table("pending_spends").delete().eq("id", spend_id).execute()

    # -- commitments (issue #76: human-delegation escrow ledger) ------------

    def save_commitment(self, commitment_id: str, data: dict[str, Any]) -> None:
        self._upsert_row("commitments", commitment_id, data)

    def load_commitments(self) -> list[dict[str, Any]]:
        return self._load_all("commitments")


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

