"""Respawn policy — fresh slate vs carry forward task scores.

Rules from artifact.md §14:
- "Respawn policy — Fresh slate vs carry forward task scores?"
- Complementary to the free-text ancestral memory (artifact.md §11): ancestral
  memory carries *lessons* as prose; the respawn policy decides whether the new
  life also inherits *quantitative task-outcome knowledge*.

This module is deliberately framework-free and deterministic (mirroring
``audit_trail.py`` / ``alert_system.py``): no LLM calls, no randomness.  It
accumulates empirical :class:`TaskOutcome` records per life, aggregates them
into per-platform / per-task-type :class:`PlatformKnowledge` (attempts,
successes, success rate, average pay, average time), and applies the configured
:class:`RespawnPolicy` on reincarnation:

- ``CARRY_FORWARD`` (default): the life's empirical task scores are archived and
  restored into the next life, so the reborn agent already knows which platform /
  task-type combos have actually paid before.
- ``FRESH_SLATE``: all empirical task knowledge is discarded on rebirth — the new
  life starts without any inherited task scores (only free-text ancestral memory).

The knowledge can be consumed by the TaskScorer as a deterministic confidence
adjustment via :meth:`RespawnPolicyEngine.knowledge_adjustment`.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Iterator, Optional

from pydantic import BaseModel


class RespawnPolicy(str, Enum):
    """How task-outcome knowledge is handled on reincarnation."""

    #: Reset all quantitative task scores on rebirth; start fresh.
    FRESH_SLATE = "fresh_slate"
    #: Archive the dead life's task scores and restore them in the next life.
    CARRY_FORWARD = "carry_forward"


#: Default confidence adjustment applied when no empirical knowledge exists yet.
_BASE_ADJUSTMENT = Decimal("0.00")
#: Max magnitude of the deterministic confidence adjustment.
_MAX_ADJUSTMENT = Decimal("0.10")
#: Minimum attempts before empirical evidence affects the adjustment at all.
_MIN_EVIDENCE = 3


class TaskOutcome(BaseModel):
    """One empirical task-execution record."""

    #: Platform the task ran on (Platform enum value, e.g. "clickworker").
    platform: str
    #: Type of task (TaskType enum value, e.g. "microtask").
    task_type: str
    #: Whether the task succeeded and paid.
    success: bool
    #: Amount earned (USD) when successful.
    amount_earned: Decimal = Decimal("0")
    #: Time spent (hours) on the task.
    time_spent_hours: Decimal = Decimal("0")


class PlatformKnowledge(BaseModel):
    """Aggregated empirical knowledge for a (platform, task_type) pair."""

    platform: str
    task_type: str
    attempts: int = 0
    successes: int = 0
    total_earned: Decimal = Decimal("0")
    total_time_hours: Decimal = Decimal("0")

    @property
    def success_rate(self) -> Decimal:
        """Fraction of attempts that succeeded, in [0, 1]."""
        if self.attempts == 0:
            return Decimal("0")
        return (Decimal(self.successes) / Decimal(self.attempts)).quantize(Decimal("0.001"))

    @property
    def avg_amount(self) -> Decimal:
        """Average amount earned per *completed* task."""
        if self.successes == 0:
            return Decimal("0")
        return (self.total_earned / Decimal(self.successes)).quantize(Decimal("0.01"))

    @property
    def avg_time_hours(self) -> Decimal:
        """Average time spent per attempt."""
        if self.attempts == 0:
            return Decimal("0")
        return (self.total_time_hours / Decimal(self.attempts)).quantize(Decimal("0.01"))

    @property
    def has_evidence(self) -> bool:
        """True once enough attempts exist for the adjustment to be meaningful."""
        return self.attempts >= 1

    def record(self, outcome: TaskOutcome) -> None:
        """Fold a single outcome into this aggregate."""
        self.attempts += 1
        if outcome.success:
            self.successes += 1
            self.total_earned += outcome.amount_earned
        self.total_time_hours += outcome.time_spent_hours

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot used for carry-forward."""
        return {
            "platform": self.platform,
            "task_type": self.task_type,
            "attempts": self.attempts,
            "successes": self.successes,
            "total_earned": str(self.total_earned),
            "total_time_hours": str(self.total_time_hours),
            "success_rate": str(self.success_rate),
            "avg_amount": str(self.avg_amount),
            "avg_time_hours": str(self.avg_time_hours),
        }


class RespawnPolicyEngine:
    """Accumulates empirical task knowledge and applies the respawn policy.

    The engine keeps *current-life* knowledge separate from the *archived*
    knowledge of past lives.  On :meth:`on_reincarnate`:

    - ``FRESH_SLATE`` wipes all current knowledge.
    - ``CARRY_FORWARD`` archives current knowledge and then restores the full
      accumulated archive into the new life, so historical task scores persist
      across rebirths.
    """

    def __init__(
        self,
        policy: RespawnPolicy | str = RespawnPolicy.CARRY_FORWARD,
    ) -> None:
        self.policy = policy if isinstance(policy, RespawnPolicy) else RespawnPolicy(policy)
        #: Effective knowledge view for the current life: the restored
        #: archive baseline (CARRY_FORWARD) plus this life's own outcomes so
        #: far. This is what queries (knowledge_for/knowledge_adjustment)
        #: read — never merged into the archive directly (see _life_delta).
        self._knowledge: dict[tuple[str, str], PlatformKnowledge] = {}
        #: This life's own new contributions only (excludes any restored
        #: baseline) — kept separate so on_reincarnate can merge just the
        #: delta into the archive without double-counting the baseline it
        #: already restored into _knowledge at the start of this life.
        self._life_delta: dict[tuple[str, str], PlatformKnowledge] = {}
        #: Ordered log of current-life outcomes (for serialisation).
        self._outcomes: list[TaskOutcome] = []
        #: Archived knowledge carried across lives (persists resets).
        self._archive: dict[tuple[str, str], PlatformKnowledge] = {}

    # -- recording -----------------------------------------------------------

    def record_outcome(
        self,
        *,
        platform: str,
        task_type: str,
        success: bool,
        amount_earned: Decimal = Decimal("0"),
        time_spent_hours: Decimal = Decimal("0"),
    ) -> TaskOutcome:
        """Record one task-execution outcome into the current life's knowledge."""
        outcome = TaskOutcome(
            platform=platform,
            task_type=task_type,
            success=success,
            amount_earned=amount_earned,
            time_spent_hours=time_spent_hours,
        )
        self._outcomes.append(outcome)
        key = (platform, task_type)
        self._knowledge.setdefault(key, PlatformKnowledge(platform=platform, task_type=task_type)).record(outcome)
        self._life_delta.setdefault(key, PlatformKnowledge(platform=platform, task_type=task_type)).record(outcome)
        return outcome

    # -- introspection -------------------------------------------------------

    def knowledge_for(self, platform: str, task_type: str) -> Optional[PlatformKnowledge]:
        """Return the aggregated knowledge for a (platform, task_type) pair."""
        return self._knowledge.get((platform, task_type))

    def __iter__(self) -> Iterator[PlatformKnowledge]:
        return iter(self._knowledge.values())

    def __len__(self) -> int:
        return len(self._knowledge)

    def knowledge_dicts(self) -> list[dict[str, Any]]:
        """Return current-life knowledge as JSON-serialisable dicts."""
        return [k.as_dict() for k in self._knowledge.values()]

    def archive_dicts(self) -> list[dict[str, Any]]:
        """Return the carry-forward archive as JSON-serialisable dicts."""
        return [k.as_dict() for k in self._archive.values()]

    def life_delta_dicts(self) -> list[dict[str, Any]]:
        """Return this life's own not-yet-archived contribution.

        Persisted separately from ``knowledge`` (the restored-baseline-plus-
        this-life effective view) so a mid-life restart doesn't lose track
        of what should actually be merged into the archive on death.
        """
        return [k.as_dict() for k in self._life_delta.values()]

    # -- evidence-based adjustment for the TaskScorer ------------------------

    def knowledge_adjustment(self, platform: str, task_type: str) -> Decimal:
        """Deterministic confidence adjustment for a (platform, task_type).

        Returns a value in [-:attr:`_MAX_ADJUSTMENT`, +:attr:`_MAX_ADJUSTMENT`]
        derived purely from empirical evidence.  Below
        :data:`_MIN_EVIDENCE` attempts the adjustment is capped and damped so a
        tiny sample cannot over-drive task selection.

        A high empirical success rate nudges confidence up; a low rate nudges it
        down.  This lets a reborn agent (``CARRY_FORWARD``) prefer what has
        actually paid in past lives.
        """
        knowledge = self._knowledge.get((platform, task_type))
        if knowledge is None or knowledge.attempts == 0:
            return _BASE_ADJUSTMENT

        # Evidence weight ramps with the number of attempts.
        evidence = min(Decimal(knowledge.attempts) / Decimal(_MIN_EVIDENCE), Decimal("1"))
        # Centre the success rate around 0.5, scale to the max magnitude.
        adjustment = (knowledge.success_rate - Decimal("0.5")) * Decimal("0.20")
        adjustment = (adjustment * evidence).quantize(Decimal("0.01"))
        adjustment = max(-_MAX_ADJUSTMENT, min(_MAX_ADJUSTMENT, adjustment))
        return adjustment

    # -- respawn -------------------------------------------------------------

    def on_reincarnate(self) -> None:
        """Apply the configured respawn policy to current-life knowledge.

        Called at the reincarnation boundary (main.py ``_reincarnate``).

        - ``FRESH_SLATE``: drop current-life task knowledge (spirit of §10: hot
          memory is wiped on death), leaving only free-text ancestral memory.
        - ``CARRY_FORWARD``: merge current knowledge into the archive, so the new
          life starts already knowing which tasks have historically paid.
        """
        if self.policy == RespawnPolicy.CARRY_FORWARD:
            # Merge only this life's OWN new contributions (_life_delta) into
            # the archive — never _knowledge, which may already contain a
            # restored archive baseline from this life's own birth; merging
            # that would double-count it back into the archive.
            for key, delta in self._life_delta.items():
                target = self._archive.setdefault(
                    key, PlatformKnowledge(platform=delta.platform, task_type=delta.task_type)
                )
                target.attempts += delta.attempts
                target.successes += delta.successes
                target.total_earned += delta.total_earned
                target.total_time_hours += delta.total_time_hours

        # The current-life outcome log and delta always reset on rebirth
        # (they're this life's raw record, not carried knowledge).
        self._outcomes.clear()
        self._life_delta.clear()
        self._knowledge.clear()

        if self.policy == RespawnPolicy.CARRY_FORWARD:
            # Restore the full archive into the new life's knowledge so it
            # actually starts knowing what has historically paid — this is
            # the whole point of CARRY_FORWARD (see class docstring).
            # model_copy() so the new life mutating its own knowledge via
            # record_outcome() can't also mutate the archived aggregates.
            for key, knowledge in self._archive.items():
                self._knowledge[key] = knowledge.model_copy()
        elif self.policy == RespawnPolicy.FRESH_SLATE:
            self._archive.clear()

    # -- persistence (Layer 1 storage) ---------------------------------------

    def to_dicts(self) -> dict[str, Any]:
        """Return full state as JSON-serialisable dicts for the carried archive."""
        return {
            "policy": self.policy.value,
            "knowledge": self.knowledge_dicts(),
            "life_delta": self.life_delta_dicts(),
            "outcomes": [o.model_dump() for o in self._outcomes],
            "archive": self.archive_dicts(),
        }

    def from_dicts(self, data: dict[str, Any]) -> None:
        """Restore state from :meth:`to_dicts` output (e.g. on startup)."""
        if not data:
            return
        raw_policy = data.get("policy", RespawnPolicy.CARRY_FORWARD.value)
        self.policy = raw_policy if isinstance(raw_policy, RespawnPolicy) else RespawnPolicy(raw_policy)

        self._archive.clear()
        for item in data.get("archive", []):
            k = PlatformKnowledge(**item)
            self._archive[(k.platform, k.task_type)] = k

        self._knowledge.clear()
        for item in data.get("knowledge", []):
            k = PlatformKnowledge(**item)
            self._knowledge[(k.platform, k.task_type)] = k

        self._life_delta.clear()
        for item in data.get("life_delta", []):
            k = PlatformKnowledge(**item)
            self._life_delta[(k.platform, k.task_type)] = k

        self._outcomes = [
            TaskOutcome(**o) for o in data.get("outcomes", [])
        ]

    def to_markdown(self) -> str:
        """Render knowledge as human-readable markdown (for the §10 diary)."""
        lines = [f"# Respawn Policy ({self.policy.value})", ""]
        if not self._knowledge:
            lines.append("_No task knowledge yet this life._")
        for k in self._knowledge.values():
            lines.append(f"## {k.platform} / {k.task_type}")
            lines.append(f"- attempts={k.attempts} successes={k.successes} "
                         f"rate={k.success_rate}")
            lines.append(f"- avg ${k.avg_amount} in {k.avg_time_hours}h")
        if self._archive:
            lines.append("")
            lines.append("### Carried-over archive")
            for k in self._archive.values():
                lines.append(f"- {k.platform} / {k.task_type}: "
                             f"{k.successes}/{k.attempts} successes "
                             f"(avg ${k.avg_amount})")
        return "\n".join(lines)
