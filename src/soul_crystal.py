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
from typing import Optional

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


def generate_soul_crystal(record: LifeRecord) -> SoulCrystal:
    """Produce a SoulCrystal from the accumulated LifeRecord."""
    now = datetime.now(timezone.utc)
    lifespan = (now - record.born_at).total_seconds() / 86400

    cause = "Debt exceeded $10.00"
    for ev in reversed(record.events):
        if "death" in ev.lower() or "failed" in ev.lower():
            cause = ev
            break

    return SoulCrystal(
        life=record.life_number,
        born=record.born_at,
        died=now,
        lifespan_days=round(lifespan, 2),
        total_earned=record.total_earned,
        peak_state=record.peak_state,
        best_platform=record.best_platform,
        best_daily_avg=record.best_daily_avg,
        failed_strategies=record.failed_strategies,
        avoid=record.avoid,
        key_lessons=record.events.copy(),
        cause_of_death=cause,
    )


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

    def on_death(self, final_debt: Decimal) -> SoulCrystal:
        """Handle death: generate crystal from current record, archive it."""
        if self.current_record is None:
            raise RuntimeError("No active life to end")

        crystal = generate_soul_crystal(self.current_record)
        self.soul_crystals.append(crystal)
        return crystal

    def get_ancestral_memory(self) -> str:
        """Build the ancestral memory block from all archived crystals."""
        return build_ancestral_memory(self.soul_crystals)

    def next_life_number(self) -> int:
        if not self.soul_crystals:
            return 1
        return max(c.life for c in self.soul_crystals) + 1
