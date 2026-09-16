"""Tests for src/delegation.py — human delegation with escrow (issue #76).

Covers the four acceptance criteria:
1. A failed/low-certainty task can produce a human-delegation Commitment
   instead of a silent $0 outcome.
2. Escrow is funded through ai_spend/approval_gate and only released (to
   human) on verified delivery — nothing is ever paid upfront.
3. Delegations surface in events, audit trail and cold archive, and survive
   in the persistence layer.
4. Deadline expiry refunds escrow to the wallet free pool.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from src.approval_gate import ApprovalGate
from src.audit_trail import AuditTrail
from src.delegation import (
    MAX_ESCROW_PER_DELEGATION,
    MIN_ESCROW_PER_DELEGATION,
    CommitmentStatus,
    DelegationError,
    DelegationHub,
)
from src.persistence import InMemoryStore
from src.scam_detection import ScamPreventionError
from src.task_executor import TaskExecutor
from src.task_scorer import PaymentMethod, TaskCandidate, TaskResult, TaskType
from src.task_scorer import Platform
from src.wallet import SpendRequest, Wallet


class RecordingArchive:
    """Cold-archive stub that records ``append_event`` calls in memory."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append_event(self, kind: str, data: dict | None = None) -> None:
        self.events.append((kind, dict(data or {})))


# Default candidate: a WRITING task on UPWORK. Small payout so the escrow
# (floored at MIN_ESCROW) lands below the §14 veto threshold and funds
# immediately; tests that need the veto path raise the payout explicitly.
def _candidate(**overrides) -> TaskCandidate:
    base = dict(
        platform=Platform.UPWORK,
        task_type=TaskType.WRITING,
        title="Write 500 words on fintech insurance",
        description="Human-quality article needed.",
        estimated_pay=Decimal("1.50"),
        estimated_hours=Decimal("2"),
        payment_method=PaymentMethod.PAYONEER,
        platform_certainty=Decimal("0.5"),
        source_url="https://example.com/job/42",
    )
    base.update(overrides)
    return TaskCandidate(**base)


@pytest.fixture
def hub() -> DelegationHub:
    store = InMemoryStore()
    wallet = Wallet(locked=Decimal("0"), free=Decimal("50.00"), debt=Decimal("0"))
    return DelegationHub(
        wallet=wallet,
        approval_gate=ApprovalGate(store),
        persistence=store,
        audit_trail=AuditTrail(),
        cold_archive=RecordingArchive(),
        event_sink=lambda msg: None,
    )


class TestFundingAndCommitment:
    def test_small_escrow_funds_immediately_through_ai_spend(self, hub: DelegationHub):
        """Small escrow: approval_gate says EXECUTED_IMMEDIATELY, ai_spend
        debits the free pool and the debit is re-tagged as escrow-held."""
        before_free = hub.wallet.free
        assert before_free == Decimal("50.00")

        result = hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="scored:upwork",
            reason="low_certainty",
        )

        assert result.status == "created"
        assert result.commitment_id
        c = hub.get(result.commitment_id)
        assert c is not None
        assert c.escrow_funded is True
        assert c.status is CommitmentStatus.PENDING
        assert c.reason == "low_certainty"
        assert c.escrow_amount == result.escrow_amount
        # Escrow came out of the free pool but is HELD, not consumed/spent.
        assert hub.wallet.free == before_free - c.escrow_amount
        assert hub.wallet.escrow == c.escrow_amount
        assert hub.wallet.total_balance == before_free
        assert hub.active_escrow_held() == c.escrow_amount

    def test_large_escrow_waits_for_veto_window(self, hub: DelegationHub):
        """Escrows at/above the veto threshold go PENDING and only get funded
        once the §14 window resolves."""
        result = hub.try_delegate(
            _candidate(estimated_pay=Decimal("50.00")),
            source_task_id="scored:upwork",
            reason="low_certainty",
        )

        assert result.status == "pending_veto"
        assert result.veto_deadline
        c = hub.get(result.commitment_id)
        assert c is not None
        assert c.escrow_funded is False
        assert c.pending_spend_id
        # Nothing has moved yet — no money held, no money spent.
        assert hub.wallet.escrow == Decimal("0.00")
        assert hub.wallet.free == Decimal("50.00")

    def test_finalize_pending_funding_holds_escrow_after_autoapprove(
        self, hub: DelegationHub
    ):
        """Mirrors main.resolve_pending_spends: ai_spend debits the free pool,
        then finalize_pending_funding re-tags the debit as escrow-held."""
        result = hub.try_delegate(
            _candidate(estimated_pay=Decimal("50.00")),
            source_task_id="scored:upwork",
            reason="low_certainty",
        )
        c = hub.get(result.commitment_id)
        assert c and c.pending_spend_id and not c.escrow_funded

        debited = hub.wallet.ai_spend(
            SpendRequest(amount=c.escrow_amount, certainty=Decimal("0.95"))
        )
        decided = hub.finalize_pending_funding(c.pending_spend_id, debited)

        assert decided is True
        assert c.escrow_funded is True
        assert c.pending_spend_id is None
        assert c.status is CommitmentStatus.PENDING
        assert hub.wallet.escrow == c.escrow_amount
        assert hub.wallet.free == Decimal("50.00") - c.escrow_amount

    def test_unknown_spend_id_finalize_is_noop(self, hub: DelegationHub):
        assert hub.finalize_pending_funding("nope", Decimal("1.00")) is False

    def test_ineligible_task_type_is_refused(self, hub: DelegationHub):
        result = hub.try_delegate(
            _candidate(task_type=TaskType.MICROTASK),
            source_task_id="x",
            reason="low_certainty",
        )
        assert result.status == "ineligible"
        assert len(hub.list_commitments()) == 0

    def test_blocked_when_debt_high(self, hub: DelegationHub):
        hub.wallet.debt = Decimal("10.00")
        result = hub.try_delegate(
            _candidate(), source_task_id="x", reason="executor_failed"
        )
        assert result.status == "blocked_debt"
        assert hub.wallet.escrow == Decimal("0.00")


class TestEscrowBudget:
    def test_capped_at_max(self, hub: DelegationHub):
        # high steady payout -> capped by the fixed per-delegation budget
        amount = hub.escrow_amount_for(_candidate(estimated_pay=Decimal("500")))
        assert amount == MAX_ESCROW_PER_DELEGATION

    def test_floor_at_min(self, hub: DelegationHub):
        # tiny-pay task still gets at least the minimum viable payout
        amount = hub.escrow_amount_for(_candidate(estimated_pay=Decimal("0.20")))
        assert amount == MIN_ESCROW_PER_DELEGATION

    def test_funding_certainty_passes_ai_spend_gate(self, hub: DelegationHub):
        # The funding spend uses the high delegation certainty, which satisfies
        # Wallet.ai_spend's 0.95 gate regardless of the failing task certainty.
        result = hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="x",
            reason="low_certainty",
        )
        assert result.status == "created"


class TestNoUpfrontPayment:
    def test_release_without_verification_is_refused(self, hub: DelegationHub):
        """§20 rule 1 is absolute: releasing escrow without verified delivery
        IS an upfront payment and the hard rule refuses it unconditionally."""
        result = hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="x",
            reason="low_certainty",
        )
        assert result.commitment_id
        c = hub.get(result.commitment_id)
        assert c
        wallet_before = hub.wallet.model_copy(deep=True)

        with pytest.raises(ScamPreventionError):
            hub.release_to_human(result.commitment_id, verification="")

        # Nothing moved: still held, still awaiting delivery.
        assert hub.wallet == wallet_before
        assert c.escrow_funded is True
        assert c.status is CommitmentStatus.PENDING
        assert hub.wallet.escrow == c.escrow_amount

    def test_release_with_verification_moves_money_out(self, hub: DelegationHub):
        hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="x",
            reason="low_certainty",
        )
        (c,) = hub.list_commitments()
        hub.claim_delivery(c.commitment_id)
        assert c.status is CommitmentStatus.DELIVERED

        amount = hub.release_to_human(
            c.commitment_id, verification="delivered artboard psd attached"
        )

        assert amount == c.escrow_amount
        assert c.status is CommitmentStatus.RELEASED
        assert c.released_at is not None
        assert "delivered artboard" in c.verification
        # Escrow pool emptied; money left the wallet entirely.
        assert hub.wallet.escrow == Decimal("0.00")
        assert hub.wallet.free == Decimal("50.00") - amount
        assert hub.active_escrow_held() == Decimal("0.00")

    def test_release_requires_active_commitment(self, hub: DelegationHub):
        with pytest.raises(DelegationError):
            hub.release_to_human("missing", verification="proof")

    def test_release_requires_funded_escrow(self, hub: DelegationHub):
        """A veto-pending commitment (not yet funded) can never be released —
        there is no escrow to release."""
        result = hub.try_delegate(
            _candidate(estimated_pay=Decimal("50.00")),
            source_task_id="x",
            reason="executor_failed",
        )
        c = hub.get(result.commitment_id)
        assert c and c.pending_spend_id and not c.escrow_funded
        with pytest.raises(DelegationError):
            hub.release_to_human(c.commitment_id, verification="proof")
        assert hub.wallet.escrow == Decimal("0.00")


class TestDeadlineExpiryRefunds:
    def _fund_one(self, hub: DelegationHub):
        result = hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="x",
            reason="low_certainty",
        )
        assert result.status == "created"
        (c,) = hub.list_commitments()
        return c

    def test_expired_commitment_refunds_escrow_to_free_pool(
        self, hub: DelegationHub
    ):
        c = self._fund_one(hub)
        escrow_held = c.escrow_amount
        free_after_funding = hub.wallet.free

        refunds = hub.expire_delegations(now=c.deadline + timedelta(seconds=1))

        assert len(refunds) == 1
        assert refunds[0]["commitment_id"] == c.commitment_id
        assert Decimal(refunds[0]["escrow_refunded"]) == escrow_held
        assert c.status is CommitmentStatus.EXPIRED
        assert hub.wallet.escrow == Decimal("0.00")
        assert hub.wallet.free == free_after_funding + escrow_held
        assert hub.wallet.total_balance == Decimal("50.00")

    def test_unexpired_commitment_not_refunded(self, hub: DelegationHub):
        c = self._fund_one(hub)
        assert hub.expire_delegations(
            now=c.deadline - timedelta(seconds=1)
        ) == []
        assert c.status is CommitmentStatus.PENDING
        assert hub.wallet.escrow == c.escrow_amount

    def test_claimed_delivery_still_refunds_on_expiry(self, hub: DelegationHub):
        c = self._fund_one(hub)
        hub.claim_delivery(c.commitment_id)
        escrow_held = c.escrow_amount

        hub.expire_delegations(now=c.deadline + timedelta(days=1))

        assert c.status is CommitmentStatus.EXPIRED
        assert hub.wallet.escrow == Decimal("0.00")
        assert hub.wallet.free == Decimal("50.00") - escrow_held + escrow_held

    def test_expiry_sweep_is_idempotent(self, hub: DelegationHub):
        c = self._fund_one(hub)
        hub.expire_delegations(now=c.deadline + timedelta(days=1))
        assert hub.expire_delegations(
            now=c.deadline + timedelta(days=2)
        ) == []
        assert hub.wallet.escrow == Decimal("0.00")

    def test_cancel_refunds_too(self, hub: DelegationHub):
        c = self._fund_one(hub)
        amount = hub.refund(c.commitment_id, CommitmentStatus.CANCELLED)
        assert amount == c.escrow_amount
        assert c.status is CommitmentStatus.CANCELLED
        assert hub.wallet.free + hub.wallet.escrow == Decimal("50.00")
        assert hub.wallet.escrow == Decimal("0.00")


class TestSurfacing:
    def test_audit_trail_records_transitions(self, hub: DelegationHub):
        events = AuditTrail()
        hub.audit_trail = events
        hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="audit:1",
            reason="low_certainty",
        )
        (c,) = hub.list_commitments()
        hub.claim_delivery(c.commitment_id)
        hub.release_to_human(c.commitment_id, verification="docs + diff")

        kind = AuditTrail.KIND_DELEGATE
        assert [e.kind for e in events].count(kind) == 3  # created, delivered, released
        created = [e for e in events if "created" in e.summary]
        assert created and "Write 500 words on fintech insurance" in created[0].summary
        released = [e for e in events if "released" in e.summary]
        # Released entry carries the verification evidence.
        assert released
        verification_line = [r for r in released[0].reasoning if r.startswith("verification=")]
        assert verification_line and "docs + diff" in verification_line[0]

    def test_cold_archive_records_escrow_events(self, hub: DelegationHub):
        archive = RecordingArchive()
        hub.cold_archive = archive
        hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="audit:1",
            reason="low_certainty",
        )
        (c,) = hub.list_commitments()
        hub.release_to_human(c.commitment_id, verification="final pdf")

        kinds = [kind for kind, _ in archive.events]
        assert "delegation_created" in kinds
        assert "escrow_released" in kinds

    def test_event_sink_receives_funding_message(self):
        sink: list[str] = []
        hub = DelegationHub(
            wallet=Wallet(free=Decimal("50.00")),
            approval_gate=ApprovalGate(InMemoryStore()),
            persistence=InMemoryStore(),
            event_sink=sink.append,
        )
        hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="x",
            reason="executor_failed",
        )
        assert any("escrow held for" in msg for msg in sink)

    def test_persistence_roundtrip(self):
        store = InMemoryStore()
        wallet = Wallet(free=Decimal("50.00"))
        hub = DelegationHub(
            wallet=wallet,
            approval_gate=ApprovalGate(store),
            persistence=store,
        )
        hub.try_delegate(
            _candidate(source_url="persist:1"),
            source_task_id="persist:1",
            reason="low_certainty",
        )

        revived = DelegationHub(
            wallet=wallet,
            approval_gate=ApprovalGate(store),
            persistence=store,
        )
        (c,) = revived.list_commitments()
        assert c.source_task_id == "persist:1"
        assert c.escrow_funded is True
        assert c.status is CommitmentStatus.PENDING
        assert c.escrow_amount == Decimal("1.50")


@pytest.fixture
def executor_task():
    """A WRITING candidate that passes the scoring threshold once
    platform_certainty is high (executor-failed path) and fails it when low
    (low-certainty path)."""
    store = InMemoryStore()
    wallet = Wallet(free=Decimal("50.00"), debt=Decimal("0.00"))
    hub = DelegationHub(
        wallet=wallet,
        approval_gate=ApprovalGate(store),
        persistence=store,
        audit_trail=AuditTrail(),
    )
    executor = TaskExecutor(wallet=wallet)
    executor.delegation = hub
    return executor, hub


class TestExecutorDelegatePath:
    @pytest.mark.asyncio
    async def test_executor_failure_produces_commitment(self, executor_task):
        """A WRITING task the agent executes but fails to complete is handed
        to a human via escrow instead of a silent $0-only outcome."""
        executor, hub = executor_task
        candidate = _candidate(platform_certainty=Decimal("0.95"))
        executor.discover_tasks = _async_return([candidate])
        failed = TaskResult(
            task_id="t1",
            candidate=candidate,
            success=False,
            amount_earned=Decimal("0"),
            error="browser closed mid-submit",
        )
        executor.execute_batch = _async_return([failed])

        results = await executor.run_earning_cycle(
            platforms=[Platform.UPWORK],
            current_debt=Decimal("0.00"),
            min_certainty=Decimal("0.85"),
        )

        assert results == [failed]
        (c,) = hub.list_commitments()
        assert c.reason == "executor_failed"
        assert c.escrow_funded is True
        assert hub.wallet.escrow == c.escrow_amount
        assert hub.wallet.free == Decimal("50.00") - c.escrow_amount

    @pytest.mark.asyncio
    async def test_low_certainty_task_delegated(self, executor_task):
        """A WRITING task the agent refuses to execute itself (scores below
        threshold) is still viable for a human — escrow is held, never spent."""
        executor, hub = executor_task
        candidate = _candidate(platform_certainty=Decimal("0.0"))
        executor.discover_tasks = _async_return([candidate])
        executor.execute_batch = _async_return([])  # should never run

        results = await executor.run_earning_cycle(
            platforms=[Platform.UPWORK],
            current_debt=Decimal("0.00"),
            min_certainty=Decimal("0.85"),
        )

        assert results == []
        (c,) = hub.list_commitments()
        assert c.reason == "low_certainty"
        assert c.escrow_funded is True
        assert hub.wallet.escrow == c.escrow_amount

    @pytest.mark.asyncio
    async def test_failed_microtask_not_delegated(self, executor_task):
        """Only inherently-human task types are delegated; a failed microtask
        keeps its $0 outcome and no commitment is created."""
        executor, hub = executor_task
        candidate = _candidate(
            platform=Platform.TOLOKA,
            task_type=TaskType.MICROTASK,
            platform_certainty=Decimal("0.95"),
        )
        executor.discover_tasks = _async_return([candidate])
        failed = TaskResult(
            task_id="t1", candidate=candidate, success=False, amount_earned=Decimal("0")
        )
        executor.execute_batch = _async_return([failed])

        results = await executor.run_earning_cycle(
            platforms=[Platform.TOLOKA],
            current_debt=Decimal("0.00"),
            min_certainty=Decimal("0.85"),
        )

        assert results == [failed]
        assert hub.list_commitments() == []
        assert hub.wallet.escrow == Decimal("0.00")


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


class TestStatusIntegrationRestoresEscrow:
    def test_wallet_restore_from_persistence_keeps_escrow(self):
        """Escrow survives a restart because persistence saves it on the
        wallet and restoration reconstructs the wallet incl. escrow."""
        store = InMemoryStore()
        wallet = Wallet(free=Decimal("20.00"))
        hub = DelegationHub(
            wallet=wallet,
            approval_gate=ApprovalGate(store),
            persistence=store,
        )
        hub.try_delegate(
            _candidate(platform_certainty=Decimal("0.0")),
            source_task_id="rst",
            reason="low_certainty",
        )
        store.save_wallet(wallet)

        data = store.load_wallet()
        restored = Wallet(
            free=Decimal(data["free"]),
            locked=Decimal(data["locked"]),
            debt=Decimal(data["debt"]),
            escrow=Decimal(data.get("escrow", "0")),
        )

        assert restored.escrow > Decimal("0")
        assert restored.free + restored.escrow == Decimal("20.00")