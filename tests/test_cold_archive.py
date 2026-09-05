"""Unit tests for cold_archive.py — HuggingFace Layer 3 cold archive.

All HuggingFace calls are mocked — no real HF_TOKEN or network needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.cold_archive import _DEFAULT_DATASET, ColdArchive, _now_iso


def _make_archive(token: str = "fake-token", dataset: str = "test/survival-ai-memory"):
    """Create a ColdArchive with a mocked HfApi client and enabled state."""
    archive = ColdArchive.__new__(ColdArchive)
    archive._dataset_name = dataset
    archive._life_number = 1
    archive._buffer = []
    archive._flush_threshold = 20
    archive._api = MagicMock()
    # By default there is no existing shard — read returns empty
    archive._api.hf_hub_download.return_value = None
    return archive


def _fake_cached_shard(tmp_path, content: str, name: str = "cached-shard.jsonl") -> str:
    """hf_hub_download returns a *local cache file path*, not file content —
    tests must mock it that way so they'd actually catch a regression of the
    "path string treated as content" bug this module used to have."""
    path = tmp_path / name
    path.write_text(content)
    return str(path)


# ---------------------------------------------------------------------------
# Graceful fallback
# ---------------------------------------------------------------------------


class TestGracefulFallback:
    """ColdArchive must not crash when HF_TOKEN is missing."""

    def test_no_token_disables(self):
        with patch.dict("os.environ", {}, clear=True):
            archive = ColdArchive()
        assert archive.enabled is False

    def test_append_event_noop_without_token(self):
        with patch.dict("os.environ", {}, clear=True):
            archive = ColdArchive()
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.flush()  # should not raise

    def test_begin_life_noop_without_token(self):
        with patch.dict("os.environ", {}, clear=True):
            archive = ColdArchive()
        archive.begin_life(2)
        archive.flush()

    def test_init_failure_disables(self):
        """If HfApi/init raises, the archive should be disabled, not crash."""
        from src.cold_archive import ColdArchive as CA
        with patch("src.cold_archive.HfApi", side_effect=RuntimeError("boom")):
            disabled = CA(dataset_name="test/x")
        assert disabled.enabled is False


# ---------------------------------------------------------------------------
# Basic buffering behavior
# ---------------------------------------------------------------------------


class TestBuffering:
    """ColdArchive buffers events and flushes as JSONL."""

    def test_append_buffers_until_threshold(self):
        archive = _make_archive()
        archive.append_event("debt_tick", {"debt": "0.50"})
        assert len(archive._buffer) == 1
        assert archive._api.upload_file.call_count == 0  # under threshold

    def test_append_flushes_at_threshold(self):
        archive = _make_archive()
        archive._flush_threshold = 2
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.append_event("state_transition", {"from": "a", "to": "b"})
        # Reaching the threshold should trigger an auto-flush
        archive._api.upload_file.assert_called_once()

    def test_flush_writes_jsonl(self):
        archive = _make_archive()
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.flush()
        archive._api.upload_file.assert_called_once()
        call = archive._api.upload_file.call_args
        assert call.kwargs["path_in_repo"] == "life-001.jsonl"
        assert call.kwargs["repo_id"] == "test/survival-ai-memory"
        assert call.kwargs["repo_type"] == "dataset"

        # Decode the uploaded body and validate it's valid JSONL
        body = call.kwargs["path_or_fileobj"].decode("utf-8")
        lines = body.strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["life"] == 1
        assert record["kind"] == "debt_tick"
        assert record["data"]["debt"] == "0.50"
        assert "ts" in record

    def test_flush_clears_buffer(self):
        archive = _make_archive()
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.flush()
        assert archive._buffer == []

    def test_flush_noop_when_disabled(self):
        archive = ColdArchive.__new__(ColdArchive)
        archive._dataset_name = "x"
        archive._life_number = 1
        archive._buffer = [{"k": "v"}]
        archive._flush_threshold = 20
        archive._api = None
        archive.flush()  # should not raise


# ---------------------------------------------------------------------------
# Life lifecycle
# ---------------------------------------------------------------------------


class TestLifeLifecycle:
    """ColdArchive tracks life numbers and begins new lives."""

    def test_begin_life_appends_life_began_event(self):
        archive = _make_archive()
        archive.begin_life(3)
        assert archive._life_number == 3
        assert len(archive._buffer) == 1
        assert archive._buffer[0]["kind"] == "life_began"
        assert archive._buffer[0]["data"]["life_number"] == 3

    def test_append_event_uses_current_life(self):
        archive = _make_archive()
        archive._life_number = 7
        archive.append_event("decision", {"x": 1})
        assert archive._buffer[0]["life"] == 7

    def test_append_event_explicit_life_overrides(self):
        archive = _make_archive()
        archive._life_number = 7
        archive.append_event("decision", {"x": 1}, life_number=9)
        assert archive._buffer[0]["life"] == 9

    def test_reset_for_new_life_drops_buffer(self):
        archive = _make_archive()
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.reset_for_new_life()
        assert archive._buffer == []


# ---------------------------------------------------------------------------
# Shard reading / appending
# ---------------------------------------------------------------------------


class TestAppending:
    """ColdArchive appends to an existing shard file."""

    def test_flush_appends_to_existing_content(self, tmp_path):
        archive = _make_archive()
        # Simulate an existing shard already on HF — hf_hub_download returns
        # a local cache file *path*, not the content itself.
        archive._api.hf_hub_download.return_value = _fake_cached_shard(tmp_path, '{"old": true}\n')
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.flush()

        call = archive._api.upload_file.call_args
        body = call.kwargs["path_or_fileobj"].decode("utf-8")
        lines = body.strip().split("\n")
        # Existing content preserved + new event appended
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"old": True}
        assert json.loads(lines[1])["kind"] == "debt_tick"

    def test_flush_reads_empty_when_no_existing(self):
        archive = _make_archive()
        archive._api.hf_hub_download.return_value = None
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.flush()
        body = archive._api.upload_file.call_args.kwargs["path_or_fileobj"].decode("utf-8")
        assert "old" not in body

    def test_flush_tolerates_download_failure(self):
        archive = _make_archive()
        archive._api.hf_hub_download.side_effect = Exception("network down")
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.flush()  # should still upload with just the new event
        body = archive._api.upload_file.call_args.kwargs["path_or_fileobj"].decode("utf-8")
        assert json.loads(body.strip().split("\n")[0])["kind"] == "debt_tick"

    def test_flush_upload_failure_keeps_buffer(self):
        archive = _make_archive()
        archive._api.upload_file.side_effect = Exception("upload failed")
        archive.append_event("debt_tick", {"debt": "0.50"})
        archive.flush()
        # Buffer retained for a later retry (event not lost)
        assert len(archive._buffer) == 1


# ---------------------------------------------------------------------------
# Setup / defaults
# ---------------------------------------------------------------------------


class TestSetup:
    def test_default_dataset_name(self):
        assert _DEFAULT_DATASET == "sagar0163/survival-ai-memory"

    def test_now_iso_format(self):
        iso = _now_iso()
        # Parseable ISO-8601 with timezone
        from datetime import datetime
        parsed = datetime.fromisoformat(iso)
        assert parsed.tzinfo is not None

    def test_enabled_true_with_api(self):
        archive = _make_archive()
        assert archive.enabled is True

    def test_uses_env_dataset_override(self):
        with patch.dict("os.environ", {"HF_DATASET": "custom/ds"}, clear=True), \
             patch("src.cold_archive.HfApi") as mock_api_cls:
            mock_api_cls.return_value.create_repo.return_value = None
            from src.cold_archive import ColdArchive as CA
            archive = CA(dataset_name=None)
        assert archive._dataset_name == "custom/ds"
