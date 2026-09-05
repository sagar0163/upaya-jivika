"""Tests for src/approval_gate.py — human approval gate (artifact.md §14)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.approval_gate import (
    VETO_THRESHOLD,
    ApprovalGate,
    PendingSpend,
    SpendDecision,
    requires_veto_window,
)
from src.persistence import InMemoryStore


class TestRequiresVetoWindow:
    def test_below_threshold_no_window(self):
        assert requires_veto_window(Decimal("1.99")) is False

    def test_at_threshold_requires_window(self):
        assert requires_veto_window(VETO_THRESHOLD) is True

    def test_above_threshold_requires_window(self):
        assert requires_veto_window(Decimal("50.00")) is True

    def test_custom_threshold(self):
        assert requires_veto_window(Decimal("3.00"), threshold=Decimal("5.00")) is False


class TestPendingSpendRoundtrip:
    def test_to_dict_from_dict_roundtrip(self):
        now = datetime.now(timezone.utc)
        pending = PendingSpend(
            spend_id="s1",
            amount=Decimal("5.00"),
            certainty=Decimal("0.97"),
            reason="2captcha solve",
            created_at=now,
            veto_deadline=now + timedelta(hours=6),
        )
        restored = PendingSpend.from_dict(pending.to_dict())
        assert restored.spend_id == "s1"
        assert restored.amount == Decimal("5.00")
        assert restored.certainty == Decimal("0.97")
        assert restored.reason == "2captcha solve"
        assert restored.rejected is False

    def test_is_expired_false_before_deadline(self):
        now = datetime.now(timezone.utc)
        pending = PendingSpend(
            spend_id="s1", amount=Decimal("5"), certainty=Decimal("0.95"),
            reason="", created_at=now, veto_deadline=now + timedelta(hours=1),
        )
        assert pending.is_expired(now) is False

    def test_is_expired_true_after_deadline(self):
        now = datetime.now(timezone.utc)
        pending = PendingSpend(
            spend_id="s1", amount=Decimal("5"), certainty=Decimal("0.95"),
            reason="", created_at=now, veto_deadline=now - timedelta(hours=1),
        )
        assert pending.is_expired(now) is True


class TestApprovalGateRequestSpend:
    def test_small_spend_executes_immediately(self):
        gate = ApprovalGate(InMemoryStore())
        decision, pending = gate.request_spend(Decimal("1.00"), Decimal("0.99"), "small experiment")
        assert decision is SpendDecision.EXECUTED_IMMEDIATELY
        assert pending is None

    def test_large_spend_creates_pending(self):
        store = InMemoryStore()
        gate = ApprovalGate(store)
        decision, pending = gate.request_spend(Decimal("5.00"), Decimal("0.97"), "2captcha solve")
        assert decision is SpendDecision.PENDING
        assert pending is not None
        assert pending.amount == Decimal("5.00")
        assert store.load_pending_spend(pending.spend_id) is not None

    def test_large_spend_persists_with_correct_deadline(self):
        gate = ApprovalGate(InMemoryStore(), window=timedelta(hours=2))
        decision, pending = gate.request_spend(Decimal("10.00"), Decimal("0.99"), "reason")
        assert pending.veto_deadline - pending.created_at == timedelta(hours=2)


class TestApprovalGateReject:
    def test_reject_pending_spend(self):
        store = InMemoryStore()
        gate = ApprovalGate(store)
        _decision, pending = gate.request_spend(Decimal("5.00"), Decimal("0.97"), "reason")

        assert gate.reject(pending.spend_id) is True
        data = store.load_pending_spend(pending.spend_id)
        assert data["rejected"] is True

    def test_reject_unknown_spend_returns_false(self):
        gate = ApprovalGate(InMemoryStore())
        assert gate.reject("nonexistent") is False

    def test_reject_expired_spend_returns_false(self):
        store = InMemoryStore()
        gate = ApprovalGate(store, window=timedelta(hours=-1))  # already expired
        _decision, pending = gate.request_spend(Decimal("5.00"), Decimal("0.97"), "reason")

        assert gate.reject(pending.spend_id) is False


class TestApprovalGateListPending:
    def test_list_pending_returns_all(self):
        store = InMemoryStore()
        gate = ApprovalGate(store)
        gate.request_spend(Decimal("5.00"), Decimal("0.97"), "a")
        gate.request_spend(Decimal("6.00"), Decimal("0.98"), "b")
        assert len(gate.list_pending()) == 2

    def test_list_pending_empty_initially(self):
        gate = ApprovalGate(InMemoryStore())
        assert gate.list_pending() == []


class TestApprovalGateResolveDue:
    def test_unexpired_spend_not_resolved(self):
        store = InMemoryStore()
        gate = ApprovalGate(store, window=timedelta(hours=6))
        gate.request_spend(Decimal("5.00"), Decimal("0.97"), "reason")
        assert gate.resolve_due() == []

    def test_expired_spend_auto_approved(self):
        store = InMemoryStore()
        gate = ApprovalGate(store, window=timedelta(hours=-1))
        _decision, pending = gate.request_spend(Decimal("5.00"), Decimal("0.97"), "reason")

        resolved = gate.resolve_due()

        assert len(resolved) == 1
        resolved_pending, decision = resolved[0]
        assert resolved_pending.spend_id == pending.spend_id
        assert decision is SpendDecision.AUTO_APPROVED
        assert store.load_pending_spend(pending.spend_id) is None

    def test_rejected_spend_resolved_as_rejected_even_before_deadline(self):
        store = InMemoryStore()
        gate = ApprovalGate(store, window=timedelta(hours=6))
        _decision, pending = gate.request_spend(Decimal("5.00"), Decimal("0.97"), "reason")
        gate.reject(pending.spend_id)

        resolved = gate.resolve_due()

        assert len(resolved) == 1
        _resolved_pending, decision = resolved[0]
        assert decision is SpendDecision.REJECTED
        assert store.load_pending_spend(pending.spend_id) is None

    def test_resolve_due_removes_from_store(self):
        store = InMemoryStore()
        gate = ApprovalGate(store, window=timedelta(hours=-1))
        gate.request_spend(Decimal("5.00"), Decimal("0.97"), "reason")
        gate.resolve_due()
        assert gate.list_pending() == []
