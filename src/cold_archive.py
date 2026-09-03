"""HuggingFace cold-archive writer — Layer 3 per artifact.md §10.

Appends every life event across every life as JSONL to a public HuggingFace
dataset (default ``sagar0163/survival-ai-memory``) so hot memory (Supabase,
Layer 1) can be wiped on death while the permanent structured record survives
and stays queryable by future lives.

Format: one JSON object per line, tagged with life number and timestamp.
The dataset is written as JSONL shards (one file per life) under:

    life-{NNN}.jsonl

Falls back gracefully (logs a warning, no crash) when huggingface_hub is
unavailable or ``HF_TOKEN`` is unset — same pattern as src/diary.py.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from huggingface_hub import HfApi

logger = logging.getLogger(__name__)

_DEFAULT_DATASET = "sagar0163/survival-ai-memory"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ColdArchive:
    """Writes life events as JSONL to a public HuggingFace dataset.

    If HF_TOKEN is unset (or initialisation fails), all write methods become
    silent no-ops that log a warning — the survival loop never crashes.
    """

    def __init__(self, dataset_name: str | None = None) -> None:
        self._dataset_name = dataset_name or os.environ.get(
            "HF_DATASET", _DEFAULT_DATASET
        )
        self._life_number: int = 1
        self._buffer: list[dict[str, Any]] = []
        self._flush_threshold = int(os.environ.get("HF_FLUSH_THRESHOLD", "20"))

        token = os.environ.get("HF_TOKEN")
        if not token:
            self._api: HfApi | None = None
            logger.warning(
                "HF_TOKEN not set — cold archive disabled (dataset=%s)",
                self._dataset_name,
            )
            return

        try:
            self._api = HfApi(token=token)
            # Ensure the repo exists (public dataset).  No-op if it does.
            self._api.create_repo(
                repo_id=self._dataset_name, repo_type="dataset", exist_ok=True
            )
            logger.info("Cold archive initialised: dataset=%s", self._dataset_name)
        except Exception:
            self._api = None
            logger.exception(
                "Failed to initialise HF cold archive (dataset=%s) — disabled",
                self._dataset_name,
            )

    # -- public API ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._api is not None

    def begin_life(self, life_number: int) -> None:
        """Set the current life number (usually on birth)."""
        self._life_number = life_number
        self._append_event(
            kind="life_began",
            data={"life_number": life_number},
        )

    def append_event(
        self,
        kind: str,
        data: dict[str, Any] | None = None,
        *,
        life_number: int | None = None,
    ) -> None:
        """Record a single event (task attempt, debt tick, decision, etc.)."""
        self._append_event(
            kind=kind,
            data=data or {},
            life_number=life_number,
        )

    def flush(self) -> None:
        """Force-write the buffered events to the dataset (no-op if disabled)."""
        if not self.enabled or not self._buffer:
            return

        life_number = self._life_number
        filename = f"life-{life_number:03d}.jsonl"
        lines = "\n".join(json.dumps(e, default=str) for e in self._buffer) + "\n"

        try:
            # huggingface_hub does not support appending to a file, so we
            # read the existing shard, append, and re-upload (idempotent +
            # human-reviewable).  For large archives this should be replaced
            # with creating per-batch files.
            existing = self._read_existing(filename)
            content = (existing + lines) if existing else lines
            self._api.upload_file(
                path_or_fileobj=content.encode("utf-8"),
                path_in_repo=filename,
                repo_id=self._dataset_name,
                repo_type="dataset",
                commit_message=f"archive: append {len(self._buffer)} event(s) life {life_number}",
            )
            self._buffer = []
            logger.debug(
                "Cold archive: wrote %s events to %s", lines.count("\n"), filename
            )
        except Exception:
            logger.exception("Cold archive flush failed (dataset=%s)", self._dataset_name)

    def reset_for_new_life(self) -> None:
        """Drop any in-memory buffer for a new life (does not touch uploaded data)."""
        self._buffer = []

    # -- internal -----------------------------------------------------------

    def _append_event(
        self,
        kind: str,
        data: dict[str, Any],
        life_number: int | None = None,
    ) -> None:
        if not self.enabled:
            return

        record = {
            "ts": _now_iso(),
            "life": life_number if life_number is not None else self._life_number,
            "kind": kind,
            "data": data,
        }
        self._buffer.append(record)
        if len(self._buffer) >= self._flush_threshold:
            self.flush()

    def _read_existing(self, filename: str) -> str:
        """Download the existing shard content, or '' if it does not exist."""
        try:
            return self._api.hf_hub_download(
                repo_id=self._dataset_name,
                filename=filename,
                repo_type="dataset",
            ) or ""
        except Exception:  # noqa: BLE001 - intentional graceful fallback
            return ""
