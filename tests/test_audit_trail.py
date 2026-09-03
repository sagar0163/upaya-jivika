"""Unit tests for src/audit_trail.py and its wiring into the decision makers.

The trail is deliberately framework-free: no LLM calls, no randomness, just a
structured append-only record. We assert the record shape, the convenience
helpers, and that TaskScorer.score / TaskExecutor.execute_task actually write
entries when an AuditTrail is supplied.
"""

import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("NVIDIA_API_KEY", "test-nvidia-key")

from src.audit_trail import AuditEntry, AuditTrail
from src.task_executor import TaskExecutor
from src.task_scorer import (
    PaymentMethod,
    Platform,
    TaskCandidate,
    TaskResult,
    TaskScorer,
    TaskType,
)
from src.wallet import Wallet

# ============================================================================
# AuditTrail core
# ============================================================================

class TestAuditTrailRecord:
    """The core record() append path."""

    def test_record_appends_entry_and_returns_it(self):
        trail = AuditTrail()
        entry = trail.record(
            actor="task_scorer",
            kind="task_scored",
            summary="Scored a task",
            reasoning=["state=THRIVING", "threshold=0.85"],
            inputs={"task": "A task", "platform": "toloka"},
            outcome={"passed": True, "final_score": "0.95"},
            survival_state="THRIVING",
            debt=Decimal("1.25"),
        )

        assert isinstance(entry, AuditEntry)
        assert len(trail) == 1
        assert trail.to_dicts()[0]["actor"] == "task_scorer"
        assert trail.to_dicts()[0]["debt"] == "1.25"
        assert trail.__iter__() is not None

    def test_record_defaults(self):
        trail = AuditTrail()
        entry = trail.record(actor="a", kind="k")
        assert entry.summary == ""
        assert entry.reasoning == []
        assert entry.inputs == {}
        assert entry.outcome == {}
        assert entry.survival_state == ""
        assert entry.debt == "0.00"
        assert entry.timestamp  # auto-generated

    def test_entries_returns_copy_not_internal_list(self):
        trail = AuditTrail()
        trail.record(actor="a", kind="k")
        snapshot = trail.entries()
        snapshot.clear()
        assert len(trail) == 1

    def test_to_dicts_is_json_serialisable(self):
        import json

        trail = AuditTrail()
        trail.record(actor="a", kind="k", debt=Decimal("2.50"))
        json.dumps(trail.to_dicts())  # must not raise


class TestAuditTrailHelpers:
    """The convenience helpers for the established decision points."""

    def test_record_task_score(self):
        trail = AuditTrail()
        entry = trail.record_task_score(
            task_title="Classify images",
            platform="toloka",
            final_score=Decimal("0.95"),
            threshold=Decimal("0.85"),
            passed=True,
            reasoning=["state=THRIVING"],
            survival_state="THRIVING",
            debt=Decimal("0.50"),
        )
        assert entry.actor == "task_scorer"
        assert entry.kind == AuditTrail.KIND_SCORE
        assert entry.outcome["passed"] is True
        assert entry.outcome["final_score"] == "0.95"
        assert "PASS" in entry.summary

    def test_record_task_execution(self):
        trail = AuditTrail()
        entry = trail.record_task_execution(
            task_id="abc123",
            task_title="Transcribe audio",
            platform="clickworker",
            success=True,
            amount_earned=Decimal("5.00"),
            time_spent_hours=Decimal("1.0"),
            error=None,
            survival_state="THRIVING",
            debt=Decimal("0.00"),
        )
        assert entry.actor == "task_executor"
        assert entry.kind == AuditTrail.KIND_EXECUTE
        assert entry.outcome["success"] is True
        assert entry.outcome["amount_earned"] == "5.00"

    def test_to_markdown_renders_reasoning(self):
        trail = AuditTrail()
        trail.record(
            actor="task_scorer",
            kind="task_scored",
            reasoning=["reason one", "reason two"],
            survival_state="THRIVING",
            debt=Decimal("1.00"),
        )
        md = trail.to_markdown()
        assert md.startswith("# Audit Trail")
        assert "reason one" in md
        assert "reason two" in md
        assert "THRIVING" in md


# ============================================================================
# Wiring into TaskScorer
# ============================================================================

class TestAuditTrailScorerWiring:
    """TaskScorer.score records a decision when given an AuditTrail."""

    def test_score_writes_audit_entry(self):
        trail = AuditTrail()
        scorer = TaskScorer(audit_trail=trail)
        candidate = TaskCandidate(
            platform=Platform.TOLOKA,
            task_type=TaskType.MICROTASK,
            title="Classify images",
            estimated_pay=Decimal("5.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )
        scorer.score(candidate, current_debt=Decimal("0.50"))

        assert len(trail) == 1
        entry = trail.entries()[0]
        assert entry.actor == "task_scorer"
        assert entry.outcome["passed"] is not None
        assert entry.survival_state == "thriving"
        assert entry.debt == "0.50"

    def test_score_no_audit_trail_is_noop(self):
        scorer = TaskScorer()  # no audit trail
        candidate = TaskCandidate(
            platform=Platform.TOLOKA,
            task_type=TaskType.MICROTASK,
            title="A task",
            estimated_pay=Decimal("5.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )
        score = scorer.score(candidate, current_debt=Decimal("0.00"))
        assert score is not None
        assert score.passes_threshold in (True, False)


# ============================================================================
# Wiring into TaskExecutor
# ============================================================================

class TestAuditTrailExecutorWiring:
    """TaskExecutor.execute_task records an entry when given an AuditTrail."""

    def _make_candidate(self) -> TaskCandidate:
        return TaskCandidate(
            platform=Platform.TOLOKA,
            task_type=TaskType.MICROTASK,
            title="Classify images",
            estimated_pay=Decimal("5.00"),
            estimated_hours=Decimal("1.0"),
            payment_method=PaymentMethod.PAYONEER,
        )

    @pytest.mark.asyncio
    async def test_execute_task_writes_audit_entry(self):
        trail = AuditTrail()
        wallet = Wallet()
        executor = TaskExecutor(wallet=wallet, headless=True, audit_trail=trail)
        executor._running = True

        connector = MagicMock()
        connector.execute_task = AsyncMock(
            return_value=TaskResult(
                task_id="task-1",
                candidate=self._make_candidate(),
                success=True,
                amount_earned=Decimal("5.00"),
                time_spent_hours=Decimal("1.0"),
                platform_data={},
            )
        )
        executor._connectors[Platform.TOLOKA] = connector

        result = await executor.execute_task(self._make_candidate())
        assert result.success is True

        assert len(trail) == 1
        entry = trail.entries()[0]
        assert entry.actor == "task_executor"
        assert entry.outcome["success"] is True
        assert entry.outcome["amount_earned"] == "5.00"
        assert entry.inputs["task_id"] == "task-1"

    @pytest.mark.asyncio
    async def test_execute_task_no_audit_trail_is_noop(self):
        wallet = Wallet()
        executor = TaskExecutor(wallet=wallet, headless=True)  # no audit trail
        executor._running = True

        connector = MagicMock()
        connector.execute_task = AsyncMock(
            return_value=TaskResult(
                task_id="task-1",
                candidate=self._make_candidate(),
                success=False,
                amount_earned=Decimal("0"),
                time_spent_hours=Decimal("0"),
                error="boom",
                platform_data={},
            )
        )
        executor._connectors[Platform.TOLOKA] = connector

        result = await executor.execute_task(self._make_candidate())
        assert result.success is False
