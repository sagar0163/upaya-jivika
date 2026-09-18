import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

from src.persistence import InMemoryStore, RESEARCH_DEDUP_WINDOW_HOURS, research_window_id
from src.audit_trail import AuditTrail
from main import SurvivalLoop

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


import asyncio

@pytest.fixture
def mock_store():
    return InMemoryStore()

@pytest.fixture
def loop(mock_store):
    l = SurvivalLoop(persistence=mock_store)
    l._restore_state()
    return l

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
    
    with patch.object(loop.persistence, 'save_wallet') as mock_save_wallet, \
         patch.object(loop.persistence, 'save_events') as mock_save_events:
        
        loop._dirty["wallet"] = True
        loop._persist_all()
        
        mock_save_wallet.assert_called_once()
        mock_save_events.assert_not_called()
        
        assert not loop._dirty["wallet"]

@pytest.mark.asyncio
async def test_research_trigger_dedup(loop):
    loop.persistence.save_last_research_at(datetime.now(timezone.utc))
    with patch.object(loop.research, 'research_earning_platforms') as mock_research:
        await loop.research_trigger()
        mock_research.assert_not_called()

