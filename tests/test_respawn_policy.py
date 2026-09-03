"""Unit tests for respawn_policy.py — fresh slate vs carry forward task scores."""

from decimal import Decimal

from src.respawn_policy import (
    PlatformKnowledge,
    RespawnPolicy,
    RespawnPolicyEngine,
    TaskOutcome,
)


class TestTaskOutcome:
    """TaskOutcome should carry one empirical execution record."""

    def test_defaults(self):
        o = TaskOutcome(platform="clickworker", task_type="microtask", success=True)
        assert o.amount_earned == Decimal("0")
        assert o.time_spent_hours == Decimal("0")

    def test_values(self):
        o = TaskOutcome(
            platform="toloka",
            task_type="microtask",
            success=True,
            amount_earned=Decimal("1.50"),
            time_spent_hours=Decimal("0.5"),
        )
        assert o.platform == "toloka"
        assert o.success is True


class TestPlatformKnowledgeAggregation:
    """Per-(platform, task_type) aggregation should fold outcomes correctly."""

    def test_empty(self):
        k = PlatformKnowledge(platform="fiverr", task_type="writing")
        assert k.attempts == 0
        assert k.success_rate == Decimal("0")
        assert k.has_evidence is False

    def test_record_success(self):
        k = PlatformKnowledge(platform="fiverr", task_type="writing")
        k.record(TaskOutcome(platform="fiverr", task_type="writing", success=True,
                             amount_earned=Decimal("5.00"), time_spent_hours=Decimal("2")))
        assert k.attempts == 1
        assert k.successes == 1
        assert k.success_rate == Decimal("1.000")
        assert k.avg_amount == Decimal("5.00")

    def test_record_mixed(self):
        k = PlatformKnowledge(platform="fiverr", task_type="writing")
        k.record(TaskOutcome(platform="fiverr", task_type="writing", success=True,
                             amount_earned=Decimal("5.00"), time_spent_hours=Decimal("2")))
        k.record(TaskOutcome(platform="fiverr", task_type="writing", success=False,
                             time_spent_hours=Decimal("1")))
        k.record(TaskOutcome(platform="fiverr", task_type="writing", success=True,
                             amount_earned=Decimal("3.00"), time_spent_hours=Decimal("1")))
        assert k.attempts == 3
        assert k.successes == 2
        assert k.success_rate == Decimal("0.667")
        assert k.avg_amount == Decimal("4.00")

    def test_has_evidence(self):
        k = PlatformKnowledge(platform="a", task_type="b")
        assert k.has_evidence is False
        k.record(TaskOutcome(platform="a", task_type="b", success=True))
        assert k.has_evidence is True


class TestRespawnDefaultCarryForward:
    """Default policy is CARRY_FORWARD — knowledge persists across rebirths."""

    def test_default_policy(self):
        engine = RespawnPolicyEngine()
        assert engine.policy == RespawnPolicy.CARRY_FORWARD

    def test_record_outcome_stores_knowledge(self):
        engine = RespawnPolicyEngine()
        engine.record_outcome(
            platform="clickworker", task_type="microtask",
            success=True, amount_earned=Decimal("2.00"), time_spent_hours=Decimal("1"),
        )
        assert len(engine) == 1
        k = engine.knowledge_for("clickworker", "microtask")
        assert k is not None
        assert k.attempts == 1

    def test_reincarnate_archives_and_restores(self):
        engine = RespawnPolicyEngine()
        engine.record_outcome(platform="clickworker", task_type="microtask", success=True)
        engine.record_outcome(platform="clickworker", task_type="microtask", success=True)
        engine.on_reincarnate()
        # Archive now exists and current knowledge was reset.
        assert len(engine._archive) == 1
        assert len(engine) == 0
        # But the aggregate view for the new life should still... current is empty;
        # history lives in the archive.

    def test_accumulates_across_multiple_reincarnations(self):
        engine = RespawnPolicyEngine()
        for _ in range(3):
            engine.record_outcome(platform="toloka", task_type="microtask", success=True)
            engine.on_reincarnate()
        assert engine._archive[("toloka", "microtask")].attempts == 3


class TestFreshSlate:
    """FRESH_SLATE should wipe all empirical task knowledge on rebirth."""

    def test_fresh_slate_wipes_everything(self):
        engine = RespawnPolicyEngine(policy=RespawnPolicy.FRESH_SLATE)
        engine.record_outcome(platform="toloka", task_type="microtask", success=True)
        engine.on_reincarnate()
        assert len(engine) == 0
        assert len(engine._archive) == 0

    def test_string_policy_coercion(self):
        engine = RespawnPolicyEngine(policy="fresh_slate")
        assert engine.policy == RespawnPolicy.FRESH_SLATE


class TestKnowledgeAdjustment:
    """Evidence-based confidence adjustment should be deterministic and clamped."""

    def test_no_knowledge_zero_adjustment(self):
        engine = RespawnPolicyEngine()
        assert engine.knowledge_adjustment("upwork", "writing") == Decimal("0.00")

    def test_high_success_positive_adjustment(self):
        engine = RespawnPolicyEngine()
        for _ in range(10):
            engine.record_outcome(platform="clickworker", task_type="microtask", success=True)
        adj = engine.knowledge_adjustment("clickworker", "microtask")
        assert adj > Decimal("0")
        assert adj <= Decimal("0.10")

    def test_low_success_negative_adjustment(self):
        engine = RespawnPolicyEngine()
        for _ in range(10):
            engine.record_outcome(platform="fiverr", task_type="writing", success=False)
        adj = engine.knowledge_adjustment("fiverr", "writing")
        assert adj < Decimal("0")
        assert adj >= Decimal("-0.10")

    def test_adjustment_clamped_to_max(self):
        engine = RespawnPolicyEngine()
        # Perfect win-rate across many attempts should hit but not exceed +0.10
        for _ in range(100):
            engine.record_outcome(platform="clickworker", task_type="microtask", success=True)
        adj = engine.knowledge_adjustment("clickworker", "microtask")
        assert adj == Decimal("0.10")

    def test_damped_with_low_evidence(self):
        engine = RespawnPolicyEngine()
        # A single success should only give a small, damped adjustment.
        engine.record_outcome(platform="clickworker", task_type="microtask", success=True)
        adj = engine.knowledge_adjustment("clickworker", "microtask")
        assert Decimal("0") < adj < Decimal("0.10")


class TestSerialization:
    """to_dicts / from_dicts should round-trip without losing data."""

    def test_round_trip_carry_forward(self):
        engine = RespawnPolicyEngine()
        engine.record_outcome(platform="toloka", task_type="microtask", success=True,
                              amount_earned=Decimal("1.00"), time_spent_hours=Decimal("0.5"))
        engine.on_reincarnate()

        data = engine.to_dicts()
        restored = RespawnPolicyEngine()
        restored.from_dicts(data)
        assert restored.policy == RespawnPolicy.CARRY_FORWARD
        assert restored._archive[("toloka", "microtask")].attempts == 1
        assert restored._archive[("toloka", "microtask")].avg_amount == Decimal("1.00")

    def test_from_dicts_empty(self):
        engine = RespawnPolicyEngine()
        engine.from_dicts({})
        assert len(engine) == 0

    def test_to_markdown(self):
        engine = RespawnPolicyEngine()
        engine.record_outcome(platform="toloka", task_type="microtask", success=True)
        md = engine.to_markdown()
        assert "Respawn Policy" in md
        assert "toloka" in md


class TestMainLoopWiring:
    """SurvivalLoop should expose respawn policy and reincarnation hook."""

    def _make_loop(self):
        from main import SurvivalLoop
        from src.persistence import InMemoryStore
        return SurvivalLoop(persistence=InMemoryStore())

    def test_survival_loop_exposes_respawn_engine(self):
        from src.respawn_policy import RespawnPolicyEngine

        loop = self._make_loop()
        assert isinstance(loop.respawn, RespawnPolicyEngine)
        assert loop.respawn.policy == RespawnPolicy.CARRY_FORWARD

    def test_record_task_outcome_delegates(self):
        loop = self._make_loop()
        loop.record_task_outcome(
            platform="clickworker", task_type="microtask",
            success=True, amount_earned=Decimal("2.00"), time_spent_hours=Decimal("1"),
        )
        k = loop.respawn.knowledge_for("clickworker", "microtask")
        assert k is not None
        assert k.attempts == 1

    def test_reincarnate_applies_policy(self):
        loop = self._make_loop()
        loop.record_task_outcome(platform="clickworker", task_type="microtask", success=True)
        loop.record_task_outcome(platform="clickworker", task_type="microtask", success=True)
        # Force a death/reincarnation through the integration path.
        from src.debt_engine import DebtState, DifficultyMode
        state = DebtState(
            life_number=1,
            debt=Decimal("10.00"),
            alive=False,
            mode=DifficultyMode.NORMAL,
        )
        loop._on_death(state)
        # Archive survives in the engine (CARRY_FORWARD), current is reset.
        assert loop.respawn._archive[("clickworker", "microtask")].attempts == 2
        assert len(loop.respawn) == 0

    def test_status_includes_respawn(self):
        loop = self._make_loop()
        status = loop.get_status()
        assert status["respawn_policy"] == "carry_forward"
        assert status["task_knowledge_entries"] == 0
