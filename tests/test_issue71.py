from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main import SurvivalLoop
from src.audit_trail import AuditTrail
from src.persistence import InMemoryStore, research_window_id


def test_atomic_research_claim():
    store = InMemoryStore()
    win_id = "r123"
    assert store.try_claim_research_window(win_id) is True
    assert store.try_claim_research_window(win_id) is False


def test_research_window_id():
    now1 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    now2 = datetime(2025, 1, 1, 15, 59, 59, tzinfo=timezone.utc)
    now3 = datetime(2025, 1, 1, 18, 0, 0, tzinfo=timezone.utc)

    assert research_window_id(now1) == research_window_id(now2)
    assert research_window_id(now1) != research_window_id(now3)


def test_bounded_audit_trail():
    trail = AuditTrail(max_entries=5)
    mock_overflow = MagicMock()
    trail._on_overflow = mock_overflow

    for i in range(10):
        trail.record(actor="tester", kind="test", summary=f"Event {i}")

    assert len(trail.entries()) == 5
    assert trail.entries()[-1].summary == "Event 9"
    assert mock_overflow.call_count == 5
    assert mock_overflow.call_args[0][0][0].summary == "Event 4"


@pytest.fixture
def mock_store():
    return InMemoryStore()


@pytest.fixture
def loop(mock_store):
    loop_inst = SurvivalLoop(persistence=mock_store)
    loop_inst._restore_state()
    return loop_inst


def test_event_log_bounds(loop):
    import main

    original_max = main.EVENT_LOG_MAX
    try:
        main.EVENT_LOG_MAX = 5
        mock_archive = MagicMock()
        loop.cold_archive = mock_archive

        for i in range(10):
            loop._log_event(f"Test {i}")

        assert len(loop._event_log) == 5
        assert loop._event_log[-1] == "Test 9"
        # Rollover should have been triggered
        assert mock_archive.append_event.called
    finally:
        main.EVENT_LOG_MAX = original_max


def test_persist_all_dirty_flags(loop):
    loop._dirty = {
        "debt_state": False,
        "wallet": False,
        "life_record": False,
        "events": False,
    }

    with patch.object(loop.persistence, "save_wallet") as mock_save_wallet, patch.object(
        loop.persistence, "save_events"
    ) as mock_save_events:
        loop._dirty["wallet"] = True
        loop._persist_all()

        mock_save_wallet.assert_called_once()
        mock_save_events.assert_not_called()

        assert not loop._dirty["wallet"]


@pytest.mark.asyncio
async def test_research_trigger_dedup(loop):
    loop.persistence.save_last_research_at(datetime.now(timezone.utc))
    with patch.object(loop.research, "research_earning_platforms") as mock_research:
        await loop.research_trigger()
        mock_research.assert_not_called()


@pytest.mark.asyncio
async def test_script_appends_events_and_records_dedup_timestamp():
    """Standalone cron script must persist event-log entries and the
    last_research_at dedup timestamp when it wins the research window,
    otherwise the in-app scheduler's fast-path check never sees the run."""
    import scripts.research_trigger as rt

    store = InMemoryStore()
    store.save_events(["existing event"])
    assert store.load_last_research_at() is None

    fake_topic = MagicMock()
    fake_topic.value = "test_topic"
    fake_result = MagicMock()
    fake_result.topic = fake_topic
    fake_result.confidence = 0.87
    fake_result.summary = "A test finding"

    fake_agent = MagicMock()
    fake_agent.research_earning_platforms = AsyncMock(return_value=[fake_result])
    fake_agent.close = AsyncMock()

    with patch.object(rt, "create_persistence_store", return_value=store), patch.object(
        rt, "ResearchAgent", return_value=fake_agent
    ), patch.object(rt, "persist_research_scores", return_value=[MagicMock()]):
        rc = await rt.main()

    assert rc == 0
    events = store.load_events()
    assert events[0] == "existing event"
    assert "Research: test_topic (confidence 0.87)" in events
    assert store.load_last_research_at() is not None