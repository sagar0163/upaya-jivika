"""Reincarnation system — Soul Crystal generation and ancestral memory.

Rules from artifact.md §11:
- On death: write death log, generate Soul Crystal (distilled life lessons),
  compress ancestral memory into next life's system prompt, start new life.
- Soul Crystal captures: lifespan, earnings, strategies, lessons, cause of death.
- Ancestral memory: all previous soul crystals compressed for the next life.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field


class SoulCrystal(BaseModel):
    """A distilled record of one life — the lessons learned."""

    life: int
    born: datetime
    died: datetime
    lifespan_days: float
    total_earned: Decimal = Decimal("0.00")
    peak_state: str = "thriving"
    best_platform: str = ""
    best_daily_avg: Decimal = Decimal("0.00")
    failed_strategies: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    key_lessons: list[str] = Field(default_factory=list)
    cause_of_death: str = ""
    # Ancestral memory carry-over: top 3 platform certainties and top 3 task-type affinities
    platform_certainties: list[tuple[str, Decimal]] = Field(default_factory=list)  # (platform, certainty) top 3
    task_affinities: list[tuple[str, Decimal]] = Field(default_factory=list)  # (task_type, affinity) top 3


class DeathLog(BaseModel):
    """Written at time of death before Soul Crystal generation."""

    life: int
    died_at: datetime
    final_debt: Decimal
    total_earned: Decimal
    peak_state: str
    cause_of_death: str
    events: list[str] = Field(default_factory=list)


class LifeRecord(BaseModel):
    """Tracks a single life's earnings and state history for crystal generation."""

    life_number: int
    born_at: datetime
    total_earned: Decimal = Decimal("0.00")
    peak_state: str = "thriving"
    events: list[str] = Field(default_factory=list)
    failed_strategies: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    best_platform: str = ""
    best_daily_avg: Decimal = Decimal("0.00")

    def record_earning(self, amount: Decimal, platform: str = "") -> None:
        self.total_earned += amount
        if platform and not self.best_platform:
            self.best_platform = platform

    def record_event(self, event: str) -> None:
        self.events.append(event)

    def record_failure(self, strategy: str, reason: str = "") -> None:
        entry = f"{strategy}" + (f" - {reason}" if reason else "")
        self.failed_strategies.append(entry)

    def record_avoid(self, item: str) -> None:
        self.avoid.append(item)


#: Number of key lessons / avoided strategies a Soul Crystal may carry (issue #63).
_MAX_LESSONS = 3
#: Max platform certainties / task affinities carried into the next life (issue #63).
_MAX_CARRIED = 3

#: Substrings that mark an event as an outcome worth distilling into a lesson,
#: as opposed to bookkeeping like "Debt tick: $0.50" or "State: … → …".
_LESSON_SIGNALS = (
    "earn",
    "paid",
    "fail",
    "reject",
    "block",
    "scam",
    "death",
    "learn",
    "debt exceeded",
)
#: Bookkeeping prefixes that never count as lessons (debt ticks in particular
#: are pure noise and lack a meaningful "learn here" signal).
_LESSON_SKIP_PREFIXES = ("debt tick:", "state:", "life ")


def select_key_lessons(events: list[str], limit: int = _MAX_LESSONS) -> list[str]:
    """Pick the ``limit`` most instructive events deterministically.

    Outcome events (earned/failed/rejected/blocked/scammed/died — anything
    matching :data:`_LESSON_SIGNALS`) are picked first, newest first; if
    fewer than ``limit`` qualify, the remaining slots are filled from the
    most recent events so the crystal's lessons read like a narrative of the
    life's end rather than a wall of every debt tick.
    """
    selected: list[str] = []
    seen: set[str] = set()
    for ev in reversed(events):
        low = ev.lower()
        if any(low.startswith(p) for p in _LESSON_SKIP_PREFIXES):
            continue
        if any(s in low for s in _LESSON_SIGNALS) and ev not in seen:
            selected.append(ev)
            seen.add(ev)
            if len(selected) >= limit:
                return selected
    for ev in reversed(events):
        if len(selected) >= limit:
            break
        if ev not in seen:
            selected.append(ev)
            seen.add(ev)
    return selected[:limit]


def select_avoided(avoid: list[str], limit: int = _MAX_LESSONS) -> list[str]:
    """Return at most ``limit`` avoided strategies for the Soul Crystal."""
    return list(avoid)[:limit]


def aggregate_top3(
    research_scores: list[Any],
) -> tuple[list[tuple[str, Decimal]], list[tuple[str, Decimal]]]:
    """Aggregate all research scores into top-3 platform certainties and task
    affinities (issue #63 ancestral carry-over).

    Each platform/task keeps its highest observed value across the life's
    research; the result is the top 3 by value — a bounded, deterministic
    summary the next life can inherit.
    """
    plat_cert: dict[str, float] = {}
    task_aff: dict[str, float] = {}
    for rs in research_scores:
        for p, c in (rs.platform_certainties or {}).items():
            plat_cert[p] = max(plat_cert.get(p, 0), float(c))
        for t, a in (rs.task_affinities or {}).items():
            task_aff[t] = max(task_aff.get(t, 0), float(a))

    def _top3(items: dict[str, float]) -> list[tuple[str, Decimal]]:
        return sorted(
            ((k, Decimal(str(v))) for k, v in items.items()),
            key=lambda x: x[1],
            reverse=True,
        )[:_MAX_CARRIED]

    return _top3(plat_cert), _top3(task_aff)


def generate_soul_crystal(
    record: LifeRecord,
    research_scores: list[Any] | None = None,
) -> SoulCrystal:
    """Produce a SoulCrystal from the accumulated LifeRecord.

    ``research_scores`` — when provided, only that life's research is distilled
    into the top-3 platform certainties / task affinities (issue #63). Falls
    back to loading from the persistence store for callers that don't have the
    scores at hand. Key lessons and avoided strategies are capped at 3 each so
    a crystal stays a bounded, distilled essence of the life.
    """
    now = datetime.now(timezone.utc)
    lifespan = (now - record.born_at).total_seconds() / 86400

    cause = "Debt exceeded $10.00"
    for ev in reversed(record.events):
        if "death" in ev.lower() or "failed" in ev.lower():
            cause = ev
            break

    # Capture top 3 platform certainties and task affinities for ancestral memory
    if research_scores is None:
        top_platforms: list[tuple[str, Decimal]] = []
        top_tasks: list[tuple[str, Decimal]] = []
        try:
            from src.persistence import create_persistence_store

            store = create_persistence_store()
            research_scores = store.load_research_scores()
            top_platforms, top_tasks = aggregate_top3(research_scores)
        except Exception:
            pass
    else:
        top_platforms, top_tasks = aggregate_top3(research_scores)

    return SoulCrystal(
        life=record.life_number,
        born=record.born_at,
        died=now,
        lifespan_days=round(lifespan, 2),
        total_earned=record.total_earned,
        peak_state=record.peak_state,
        best_platform=record.best_platform,
        best_daily_avg=record.best_daily_avg,
        failed_strategies=record.failed_strategies[:_MAX_LESSONS],
        avoid=select_avoided(record.avoid),
        key_lessons=select_key_lessons(record.events),
        cause_of_death=cause,
        platform_certainties=top_platforms,
        task_affinities=top_tasks,
    )


def carry_over_top3(
    crystals: list[SoulCrystal],
) -> tuple[list[tuple[str, Decimal]], list[tuple[str, Decimal]]]:
    """Aggregate the carried certainties/affinities from all soul crystals.

    Every crystal's per-life top 3 is merged, keeping the highest value per
    platform/task, and the result is bounded to the top 3 again — so across
    many lives the inherited block stays a fixed size (never god-mode, never
    overflow) and fresh, higher-certainty research in the *current* life
    naturally replaces stale inherited wisdom on the next death.
    """
    plat_cert: dict[str, Decimal] = {}
    task_aff: dict[str, Decimal] = {}
    for crystal in crystals:
        for p, c in crystal.platform_certainties:
            plat_cert[p] = max(plat_cert.get(p, Decimal("0")), c)
        for t, a in crystal.task_affinities:
            task_aff[t] = max(task_aff.get(t, Decimal("0")), a)

    def _top3_sorted(items: dict[str, Decimal]) -> list[tuple[str, Decimal]]:
        return sorted(items.items(), key=lambda x: x[1], reverse=True)[:_MAX_CARRIED]

    return _top3_sorted(plat_cert), _top3_sorted(task_aff)


def build_carry_over_research_scores(crystals: list[SoulCrystal]) -> list[Any]:
    """Seed a new life's research table from past lives' carried top-3.

    Returns a single :class:`~src.persistence.ResearchScore` (or an empty list
    when there is no inheritance yet) whose certainties/affinities are exactly
    the aggregated top-3 carried over from the soul-crystal archive. The
    TaskScorer reads research scores directly, so the reborn life immediately
    prefers what past lives' research deemed most certain — without a fresh
    research cycle — but only the bounded top-3, so it still starts far from
    god-mode.
    """
    if not crystals:
        return []
    platforms, tasks = carry_over_top3(crystals)
    if not platforms and not tasks:
        return []

    from src.persistence import ResearchScore

    return [
        ResearchScore(
            topic="ancestral_carry_over",
            query="inherent top-3 platform certainties / task affinities from past lives",
            findings=[],
            summary=(
                "Ancestral carry-over: top-3 platform certainties and task "
                "affinities inherited from past soul crystals (issue #63)."
            ),
            confidence=min((c for _, c in platforms), default=0.0),
            sources=[],
            platform_certainties={p: float(c) for p, c in platforms},
            task_affinities={t: float(a) for t, a in tasks},
        )
    ]


def generate_death_log(record: LifeRecord, final_debt: Decimal) -> DeathLog:
    """Produce a death log from the LifeRecord."""
    return DeathLog(
        life=record.life_number,
        died_at=datetime.now(timezone.utc),
        final_debt=final_debt,
        total_earned=record.total_earned,
        peak_state=record.peak_state,
        cause_of_death=f"Debt exceeded $10.00 (${final_debt})",
        events=record.events.copy(),
    )


def build_ancestral_memory(crystals: list[SoulCrystal]) -> str:
    """Compress all soul crystals into an ancestral memory block for the next life's
    system prompt.

    In production this would use NVIDIA NIM to compress. Here we build a
    structured summary that a future LLM call can ingest.
    """
    if not crystals:
        return ""

    lines = ["=== ANCESTRAL MEMORY ==="]
    for c in crystals:
        lines.append(f"\nLife {c.life} ({c.lifespan_days} days, earned ${c.total_earned}):")
        lines.append(f"  Peak state: {c.peak_state}")
        if c.best_platform:
            lines.append(f"  Best platform: {c.best_platform}")
        if c.cause_of_death:
            lines.append(f"  Died: {c.cause_of_death}")
        for lesson in c.key_lessons:
            lines.append(f"  Lesson: {lesson}")
        for fail in c.failed_strategies:
            lines.append(f"  FAILED: {fail}")
        for avoid_item in c.avoid:
            lines.append(f"  AVOID: {avoid_item}")
        
        if c.platform_certainties:
            certs = ", ".join([f"{p} ({cert:.2f})" for p, cert in c.platform_certainties])
            lines.append(f"  Top Platforms (certainty): {certs}")
        if c.task_affinities:
            affs = ", ".join([f"{t} ({aff:.2f})" for t, aff in c.task_affinities])
            lines.append(f"  Top Task Affinities: {affs}")

    total_lives = len(crystals)
    total_earned = sum(c.total_earned for c in crystals)
    total_days = sum(c.lifespan_days for c in crystals)
    lines.append(f"\n=== SUMMARY: {total_lives} lives, {total_days:.0f} days, ${total_earned} earned ===")

    return "\n".join(lines)


class ReincarnationEngine:
    """Orchestrates death → crystal → rebirth."""

    def __init__(self) -> None:
        self.soul_crystals: list[SoulCrystal] = []
        self.current_record: Optional[LifeRecord] = None

    def start_new_life(self, life_number: int) -> LifeRecord:
        """Begin tracking a new life."""
        self.current_record = LifeRecord(
            life_number=life_number,
            born_at=datetime.now(timezone.utc),
        )
        return self.current_record

    def on_death(
        self,
        final_debt: Decimal,
        research_scores: list[Any] | None = None,
    ) -> SoulCrystal:
        """Handle death: generate crystal from current record, archive it.

        ``research_scores`` (issue #63): the dying life's own research scores,
        distilled into the crystal's top-3 platform certainties / task
        affinities so the next life inherits exactly what this life learned.
        Falls back to ``generate_soul_crystal``'s store-load when omitted.
        """
        if self.current_record is None:
            raise RuntimeError("No active life to end")

        crystal = generate_soul_crystal(self.current_record, research_scores)
        self.soul_crystals.append(crystal)
        return crystal

    def get_ancestral_memory(self) -> str:
        """Build the ancestral memory block from all archived crystals."""
        return build_ancestral_memory(self.soul_crystals)

    def next_life_number(self) -> int:
        if not self.soul_crystals:
            return 1
        return max(c.life for c in self.soul_crystals) + 1
