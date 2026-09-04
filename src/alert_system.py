"""Alert system - notify the user at Critical/Terminal survival states.

Rules from artifact.md §14:
- "Alert system - Notify user at Critical/Terminal (email? SMS?)"
- This is the 🟡 gap that raises a structured :class:`Alert` whenever the
  survival state first enters a danger zone (Critical/Terminal/Dead).

Design constraints (mirroring ``audit_trail.py``):
- Framework-free and deterministic: no LLM calls, no randomness.
- The alert *store* is in-memory + appends; durable delivery is delegated to
  pluggable :class:`AlertNotifier` channels so the module ships with no
  external credentials.  The default channel is a :class:`LogNotifier` that
  writes to the standard logging subsystem; a :class:`NoopNotifier` is also
  provided for silent/test environments.
- Wired into the survival loop: a transition *into* Critical/Terminal/Dead
  fires an alert once (deduplicated), and a death fires a TERMINAL alert.

To add real delivery later (email/SMS/webhook), implement :class:`AlertNotifier`
and pass the channel to :class:`AlertSystem` - the rest of the pipeline is
unchanged.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Iterator, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    """Return a tz-aware UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class AlertLevel(str, Enum):
    """Severity of a survival alert."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    TERMINAL = "terminal"


class Alert(BaseModel):
    """A single structured survival alert."""

    #: ISO-8601 UTC timestamp of when the alert was raised.
    timestamp: str = Field(default_factory=_utc_now)
    #: Severity level (see :class:`AlertLevel`).
    level: AlertLevel
    #: Survival state that triggered the alert (state_machine value).
    state: str
    #: Human-readable alert message.
    message: str
    #: Debt (in USD) at the time of the alert.
    debt: str = "0.00"
    #: Optional extra context (JSON-serialisable).
    context: dict[str, Any] = Field(default_factory=dict)


class AlertNotifier(ABC):
    """A delivery channel for :class:`Alert` messages.

    Implementations must not raise; :meth:`send` returns True if the alert
    was delivered, False if it was skipped.
    """

    @abstractmethod
    def send(self, alert: Alert) -> bool:
        """Deliver an alert. Return True on success, False to skip."""


class LogNotifier(AlertNotifier):
    """Default channel - routes alerts through the standard logging subsystem.

    Critical/terminal alerts are surfaced at WARNING level so they are visible
    in normal operation without external credentials.
    """

    def send(self, alert: Alert) -> bool:
        logger.warning(
            "[%s] %s (state=%s, debt=$%s) %s",
            alert.level.value.upper(),
            alert.message,
            alert.state,
            alert.debt,
            alert.context,
        )
        return True


class NoopNotifier(AlertNotifier):
    """Silent channel - accepts but discards alerts (for tests)."""

    def send(self, alert: Alert) -> bool:
        return False


#: States that trigger a danger alert on entry, mapped to their severity.
DANGER_STATES: dict[str, AlertLevel] = {
    "critical": AlertLevel.CRITICAL,
    "terminal": AlertLevel.TERMINAL,
    "dead": AlertLevel.TERMINAL,
}


class AlertSystem:
    """Raises, stores, and dispatches survival alerts.

    Usage::

        alerts = AlertSystem()
        alerts.on_state_change(previous="struggling", current="critical", debt=Decimal("8.0"))
        alerts.on_death(debt=Decimal("10.0"), life_number=3)

    Alerts are stored in memory and dispatched to the configured notifiers.
    ``on_state_change`` only fires for a given danger state once per
    :class:`AlertSystem` instance (deduplicated), so a sustained danger state
    does not spam the channel.
    """

    #: Danger states to alert on plus their severities.
    danger_states: dict[str, AlertLevel] = DANGER_STATES

    def __init__(
        self,
        notifiers: Optional[Iterable[AlertNotifier]] = None,
        enabled: bool = True,
    ) -> None:
        #: Delivery channels. Defaults to the logging channel.
        self._notifiers: list[AlertNotifier] = list(notifiers) if notifiers else [LogNotifier()]
        #: Whether alerts are currently raised/dispatched.
        self.enabled = enabled
        self._alerts: list[Alert] = []
        #: Danger states already alerted for this instance (dedup key).
        self._alerted_states: set[str] = set()

    # -- raising ------------------------------------------------------------

    def raise_alert(
        self,
        *,
        level: AlertLevel | str,
        state: str,
        message: str,
        debt: Optional[Decimal] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> Alert:
        """Create, store, and dispatch an alert. Returns the alert."""
        _level = level if isinstance(level, AlertLevel) else AlertLevel(level)
        alert = Alert(
            level=_level,
            state=state,
            message=message,
            debt=str(debt) if debt is not None else "0.00",
            context=dict(context or {}),
        )
        self._alerts.append(alert)

        if self.enabled:
            for notifier in self._notifiers:
                try:
                    notifier.send(alert)
                except Exception:  # noqa: BLE001 - notifiers must not break the loop
                    logger.exception("Alert notifier failed for level=%s", _level.value)

        return alert

    def on_state_change(
        self,
        *,
        previous: str,
        current: str,
        debt: Decimal,
    ) -> Optional[Alert]:
        """Alert once when the agent transitions *into* a danger state.

        Returns the alert if one was raised, else None (e.g. the current state
        is not a danger state, or it was already alerted for this instance).
        """
        level = self.danger_states.get(current)
        if level is None:
            return None
        if current in self._alerted_states:
            return None

        self._alerted_states.add(current)
        return self.raise_alert(
            level=level,
            state=current,
            message=f"Survival state became {current} (was {previous}) — debt ${debt}",
            debt=debt,
            context={"previous": previous, "current": current},
        )

    def on_death(self, *, debt: Decimal, life_number: int) -> Alert:
        """Alert on death (Terminal - irreversible)."""
        self._alerted_states.add("dead")
        return self.raise_alert(
            level=AlertLevel.TERMINAL,
            state="dead",
            message=(
                f"DEATH - life {life_number} ended at debt ${debt}. "
                "Permanently shut down; generating soul crystal."
            ),
            debt=debt,
            context={"life_number": life_number},
        )

    # -- control ------------------------------------------------------------

    def reset(self) -> None:
        """Clear the dedup set (e.g. on reincarnation) without wiping history."""
        self._alerted_states.clear()

    # -- introspection --------------------------------------------------------

    def __iter__(self) -> Iterator[Alert]:
        return iter(self._alerts)

    def __len__(self) -> int:
        return len(self._alerts)

    def alerts(self) -> list[Alert]:
        """Return a copy of all alerts raised so far."""
        return list(self._alerts)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Return all alerts as JSON-serialisable dicts (for durable storage)."""
        return [a.model_dump() for a in self._alerts]

    def to_markdown(self) -> str:
        """Render the alert history as human-readable markdown."""
        lines = ["# Alert History", ""]
        for alert in self._alerts:
            lines.append(f"## {alert.timestamp}")
            lines.append(f"- **Level**: {alert.level.value.upper()}")
            lines.append(f"- **State**: {alert.state} / debt ${alert.debt}")
            lines.append(f"- **Message**: {alert.message}")
            if alert.context:
                lines.append(f"- **Context**: {alert.context}")
            lines.append("")
        return "\n".join(lines)