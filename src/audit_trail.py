"""Audit trail - every decision logged with full reasoning.

Rules from artifact.md §14:
- "Audit trail - Every decision logged with full reasoning"
- Write frequency (§10): every task attempt, every debt tick, every state
  change, every decision.

This module provides a structured, append-only record of every decision the
agent makes.  Each :class:`AuditEntry` captures *what* was decided, *why*
(full reasoning), the inputs that fed the decision, and the outcome plus the
survival context (state + debt at the time).

It is deliberately framework-free and deterministic: no LLM calls, no random
behaviour - just structured logging of decisions made elsewhere.  The core
decision points (task scoring, task execution) are wired to write audit
entries when an :class:`AuditTrail` is supplied.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator, Optional

from pydantic import BaseModel, Field


def _utc_now() -> str:
    """Return a tz-aware UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class AuditEntry(BaseModel):
    """A single logged decision."""

    #: ISO-8601 UTC timestamp of when the decision was recorded.
    timestamp: str = Field(default_factory=_utc_now)
    #: Which module/actor made the decision (e.g. "task_scorer", "task_executor").
    actor: str
    #: What kind of decision this is (e.g. "task_scored", "task_executed").
    kind: str
    #: One-line human-readable summary of the decision.
    summary: str = ""
    #: Full reasoning - the *why* behind the decision.
    reasoning: list[str] = Field(default_factory=list)
    #: Raw inputs that fed the decision (JSON-serialisable).
    inputs: dict[str, Any] = Field(default_factory=dict)
    #: The outcome expressed as JSON-serialisable values.
    outcome: dict[str, Any] = Field(default_factory=dict)
    #: Survival state at the time of the decision (from state_machine).
    survival_state: str = ""
    #: Debt (in USD) at the time of the decision.
    debt: str = "0.00"


class AuditTrail:
    """Append-only, structured decision log.

    Entries are kept in memory and can be serialised to JSON-serialisable
    dicts via :meth:`to_dicts`.  The store is intentionally simple: callers
    that need durable persistence (e.g. Supabase / GitHub diary) can consume
    :meth:`to_dicts` and write them to Layer 1/2/3 storage (artifact.md §10).
    """

    KIND_SCORE = "task_scored"
    KIND_EXECUTE = "task_executed"

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(
        self,
        *,
        actor: str,
        kind: str,
        summary: str = "",
        reasoning: Optional[list[str]] = None,
        inputs: Optional[dict[str, Any]] = None,
        outcome: Optional[dict[str, Any]] = None,
        survival_state: str = "",
        debt: Optional[Decimal] = None,
    ) -> AuditEntry:
        """Append and return a new audit entry."""
        entry = AuditEntry(
            actor=actor,
            kind=kind,
            summary=summary,
            reasoning=list(reasoning or []),
            inputs=dict(inputs or {}),
            outcome=dict(outcome or {}),
            survival_state=survival_state,
            debt=str(debt) if debt is not None else "0.00",
        )
        self._entries.append(entry)
        return entry

    # -- convenience helpers for the established decision points -------------

    def record_task_score(
        self,
        *,
        task_title: str,
        platform: str,
        final_score: Decimal,
        threshold: Decimal,
        passed: bool,
        reasoning: list[str],
        survival_state: str,
        debt: Decimal,
    ) -> AuditEntry:
        """Record a task-scoring decision (the scorer's pass/fail call)."""
        return self.record(
            actor="task_scorer",
            kind=self.KIND_SCORE,
            summary=(
                f"Task '{task_title}' on {platform} scored {final_score} "
                f"vs threshold {threshold} -> {'PASS' if passed else 'REJECT'}"
            ),
            reasoning=reasoning,
            inputs={"task": task_title, "platform": platform},
            outcome={"final_score": str(final_score), "threshold": str(threshold), "passed": passed},
            survival_state=survival_state,
            debt=debt,
        )

    def record_task_execution(
        self,
        *,
        task_id: str,
        task_title: str,
        platform: str,
        success: bool,
        amount_earned: Decimal,
        time_spent_hours: Decimal,
        error: Optional[str],
        survival_state: str,
        debt: Decimal,
    ) -> AuditEntry:
        """Record a task-execution decision and its outcome."""
        return self.record(
            actor="task_executor",
            kind=self.KIND_EXECUTE,
            summary=(
                f"Executed task '{task_title}' on {platform} "
                f"-> {'success' if success else 'failed'}"
            ),
            reasoning=[
                f"task_id={task_id}",
                f"amount_earned=${amount_earned}",
                f"time_spent_hours={time_spent_hours}",
            ],
            inputs={"task_id": task_id, "task": task_title, "platform": platform},
            outcome={
                "success": success,
                "amount_earned": str(amount_earned),
                "time_spent_hours": str(time_spent_hours),
                "error": error or "",
            },
            survival_state=survival_state,
            debt=debt,
        )

    # -- introspection --------------------------------------------------------

    def __iter__(self) -> Iterator[AuditEntry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[AuditEntry]:
        """Return a copy of all entries recorded so far."""
        return list(self._entries)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Return all entries as JSON-serialisable dicts (for durable storage)."""
        return [e.model_dump() for e in self._entries]

    def to_markdown(self) -> str:
        """Render the trail as human-readable markdown (for the §10 diary)."""
        lines = ["# Audit Trail", ""]
        for entry in self._entries:
            lines.append(f"## {entry.timestamp}")
            lines.append(f"- **Actor**: {entry.actor}")
            lines.append(f"- **Kind**: {entry.kind}")
            lines.append(f"- **Summary**: {entry.summary}")
            lines.append(f"- **State**: {entry.survival_state} / debt ${entry.debt}")
            if entry.reasoning:
                lines.append("- **Reasoning**:")
                lines.extend(f"  - {r}" for r in entry.reasoning)
            lines.append("")
        return "\n".join(lines)
