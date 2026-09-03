"""Unit tests for state_machine.py — survival state transitions."""

from decimal import Decimal

import pytest

from src.state_machine import (
    State,
    SurvivalStateMachine,
    min_certainty,
    resolve_state,
    risk_tolerance,
)


class TestResolveState:
    """Each debt range should map to the correct state."""

    @pytest.mark.parametrize(
        "debt,expected",
        [
            (Decimal("0.00"), State.THRIVING),
            (Decimal("1.00"), State.THRIVING),
            (Decimal("1.99"), State.THRIVING),
            (Decimal("2.00"), State.SURVIVING),
            (Decimal("3.50"), State.SURVIVING),
            (Decimal("4.99"), State.SURVIVING),
            (Decimal("5.00"), State.STRUGGLING),
            (Decimal("6.00"), State.STRUGGLING),
            (Decimal("7.49"), State.STRUGGLING),
            (Decimal("7.50"), State.CRITICAL),
            (Decimal("8.50"), State.CRITICAL),
            (Decimal("9.49"), State.CRITICAL),
            (Decimal("9.50"), State.TERMINAL),
            (Decimal("9.99"), State.TERMINAL),
            (Decimal("10.00"), State.DEAD),
            (Decimal("15.00"), State.DEAD),
        ],
    )
    def test_debt_to_state(self, debt, expected):
        assert resolve_state(debt) == expected


class TestRiskTolerance:
    @pytest.mark.parametrize(
        "debt,expected_risk",
        [
            (Decimal("0.00"), Decimal("0.70")),
            (Decimal("2.00"), Decimal("0.50")),
            (Decimal("5.00"), Decimal("0.20")),
            (Decimal("7.50"), Decimal("0.00")),
            (Decimal("9.50"), Decimal("0.00")),
        ],
    )
    def test_risk_by_debt(self, debt, expected_risk):
        assert risk_tolerance(debt) == expected_risk


class TestMinCertainty:
    @pytest.mark.parametrize(
        "debt,expected",
        [
            (Decimal("0.00"), Decimal("0.85")),
            (Decimal("3.00"), Decimal("0.85")),
            (Decimal("6.00"), Decimal("0.70")),
            (Decimal("8.00"), Decimal("0.60")),
            (Decimal("9.60"), Decimal("0.50")),
        ],
    )
    def test_certainty_gates(self, debt, expected):
        assert min_certainty(debt) == expected


class TestStateMachineTransitions:
    def test_no_transition_on_same_state(self):
        sm = SurvivalStateMachine()
        result = sm.update(Decimal("1.00"))
        assert result is None  # still thriving

    def test_transition_fires(self):
        sm = SurvivalStateMachine()
        transitions = []
        sm._on_transition = lambda t: transitions.append(t)
        sm.update(Decimal("3.00"))  # thriving → surviving
        assert len(transitions) == 1
        assert transitions[0].previous == State.THRIVING
        assert transitions[0].current == State.SURVIVING

    def test_sequential_transitions(self):
        sm = SurvivalStateMachine()
        t1 = sm.update(Decimal("3.00"))
        assert t1.current == State.SURVIVING
        t2 = sm.update(Decimal("6.00"))
        assert t2.current == State.STRUGGLING
        t3 = sm.update(Decimal("8.00"))
        assert t3.current == State.CRITICAL
        t4 = sm.update(Decimal("9.70"))
        assert t4.current == State.TERMINAL
        t5 = sm.update(Decimal("10.00"))
        assert t5.current == State.DEAD

    def test_is_alive(self):
        sm = SurvivalStateMachine()
        assert sm.is_alive() is True
        sm.update(Decimal("10.00"))
        assert sm.is_alive() is False

    def test_reset(self):
        sm = SurvivalStateMachine()
        sm.update(Decimal("10.00"))
        assert sm.state == State.DEAD
        sm.reset()
        assert sm.state == State.THRIVING
