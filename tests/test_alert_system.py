"""Unit tests for src/alert_system.py and its wiring into the survival loop.

The alert system is deliberately framework-free: no LLM calls, no randomness,
just structured alerts dispatched to pluggable notifiers. We assert the alert
shape, the level logic, the deduplication on entering danger states, the
default/silent channels, and that SurvivalLoop raises alerts on Critical/
Terminal transitions and on death.
"""

import os
from decimal import Decimal

os.environ.setdefault("NVIDIA_API_KEY", "test-nvidia-key")

from src.alert_system import (
    Alert,
    AlertLevel,
    AlertSystem,
    LogNotifier,
    NoopNotifier,
)

# ============================================================================
# Core AlertSystem
# ============================================================================

class TestAlertSystemRecord:
    """The core raise_alert() append + dispatch path."""

    def test_raise_alert_appends_and_returns(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alert = alerts.raise_alert(
            level=AlertLevel.CRITICAL,
            state="critical",
            message="Danger",
            debt=Decimal("8.00"),
            context={"previous": "struggling"},
        )
        assert isinstance(alert, Alert)
        assert len(alerts) == 1
        assert alerts.to_dicts()[0]["state"] == "critical"
        assert alerts.to_dicts()[0]["debt"] == "8.00"

    def test_raise_alert_accepts_string_level(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alert = alerts.raise_alert(level="warning", state="struggling", message="x")
        assert alert.level == AlertLevel.WARNING

    def test_raise_alert_defaults(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alert = alerts.raise_alert(level=AlertLevel.INFO, state="thriving", message="x")
        assert alert.debt == "0.00"
        assert alert.context == {}
        assert alert.timestamp  # auto-generated

    def test_alerts_returns_copy_not_internal_list(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alerts.raise_alert(level="info", state="thriving", message="x")
        snapshot = alerts.alerts()
        snapshot.clear()
        assert len(alerts) == 1

    def test_to_dicts_is_json_serialisable(self):
        import json

        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alerts.raise_alert(level="critical", state="critical", message="x",
                           debt=Decimal("9.00"))
        json.dumps(alerts.to_dicts())  # must not raise

    def test_to_markdown_renders_level_and_message(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alerts.raise_alert(
            level="terminal",
            state="dead",
            message="DEATH - life 1 ended",
            context={"life_number": 1},
        )
        md = alerts.to_markdown()
        assert md.startswith("# Alert History")
        assert "DEATH - life 1 ended" in md
        assert "TERMINAL" in md


class TestNotifiers:
    """The pluggable delivery channels."""

    def test_log_notifier_returns_true(self, caplog):
        import logging

        notifier = LogNotifier()
        alert = Alert(level=AlertLevel.CRITICAL, state="critical", message="boom")
        with caplog.at_level(logging.WARNING):
            assert notifier.send(alert) is True
        assert "boom" in caplog.text

    def test_noop_notifier_discards(self):
        notifier = NoopNotifier()
        alert = Alert(level=AlertLevel.CRITICAL, state="critical", message="boom")
        assert notifier.send(alert) is False


# ============================================================================
# Danger-state deduplication
# ============================================================================

class TestDangerStateDedup:
    """on_state_change fires once per danger state per instance."""

    def test_enter_critical_raises_alert(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alert = alerts.on_state_change(
            previous="struggling", current="critical", debt=Decimal("8.00")
        )
        assert alert is not None
        assert alert.level == AlertLevel.CRITICAL
        assert alert.state == "critical"

    def test_enter_terminal_raises_alert(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alert = alerts.on_state_change(
            previous="critical", current="terminal", debt=Decimal("9.75")
        )
        assert alert is not None
        assert alert.level == AlertLevel.TERMINAL

    def test_non_danger_state_no_alert(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alert = alerts.on_state_change(
            previous="thriving", current="surviving", debt=Decimal("2.00")
        )
        assert alert is None
        assert len(alerts) == 0

    def test_duplicate_danger_state_no_second_alert(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        first = alerts.on_state_change(
            previous="struggling", current="critical", debt=Decimal("8.00")
        )
        second = alerts.on_state_change(
            previous="critical", current="critical", debt=Decimal("9.00")
        )
        assert first is not None
        assert second is None  # deduplicated
        assert len(alerts) == 1

    def test_reset_allows_realerting(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alerts.on_state_change(
            previous="struggling", current="critical", debt=Decimal("8.00")
        )
        alerts.reset()
        again = alerts.on_state_change(
            previous="thriving", current="critical", debt=Decimal("8.00")
        )
        assert again is not None
        assert len(alerts) == 2  # history preserved across reset

    def test_on_death_raises_terminal_alert(self):
        alerts = AlertSystem(notifiers=[NoopNotifier()])
        alert = alerts.on_death(debt=Decimal("10.00"), life_number=3)
        assert alert is not None
        assert alert.level == AlertLevel.TERMINAL
        assert alert.state == "dead"
        assert alert.debt == "10.00"


# ============================================================================
# Wiring into SurvivalLoop
# ============================================================================

class TestSurvivalLoopAlertWiring:
    """SurvivalLoop raises alerts when the agent enters danger and on death."""

    def _make_loop(self):
        from main import SurvivalLoop
        from tests.test_main_loop import InMemoryStore

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)
        loop.research.research_earning_platforms = __import__(
            "unittest.mock", fromlist=["AsyncMock"]
        ).AsyncMock(return_value=[])
        # Silence the default logging channel for deterministic assertions
        loop.alerts._notifiers = [NoopNotifier()]
        return loop, store

    def test_transition_to_critical_raises_alert(self):
        loop, _ = self._make_loop()
        # ~16 ticks: 8.00 -> CRITICAL (from STRUGGLING at 5.00-7.50)
        for _ in range(16):
            loop.debt_tick()
        assert loop.state_machine.state == "critical"
        assert len(loop.alerts) == 1
        entry = loop.alerts.alerts()[0]
        assert entry.level == AlertLevel.CRITICAL
        assert entry.state == "critical"

    def test_critical_alert_not_duplicated_on_more_ticks(self):
        loop, _ = self._make_loop()
        for _ in range(16):
            loop.debt_tick()
        for _ in range(2):  # stays critical
            loop.debt_tick()
        assert len(loop.alerts) == 1

    def test_death_raises_terminal_alert(self):
        from unittest.mock import MagicMock

        loop, _ = self._make_loop()
        # Ensure diary/research don't reach the network
        loop.diary = MagicMock()
        loop.diary.on_tick = MagicMock()
        loop.diary.on_death = MagicMock()
        loop.reincarnation.get_ancestral_memory = MagicMock(return_value="Life 1")

        # Fast-forward to death ($10.00)
        for _ in range(20):
            loop.debt_tick()

        # Death triggers an alert and then automatic reincarnation
        levels = {a.level for a in loop.alerts}
        assert AlertLevel.TERMINAL in levels
        assert AlertLevel.CRITICAL in levels  # transitioned through critical first