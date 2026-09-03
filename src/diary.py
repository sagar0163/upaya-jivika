"""GitHub diary writer — Layer 2 narrative diary per artifact.md §10.

Writes human-readable markdown diary entries and JSON data to a GitHub repo
using PyGithub.  Structured as:

    life-{NNN}/day-{NN}.md       — daily narrative (state, debt, events)
    life-{NNN}/death-note.md     — written on death
    life-{NNN}/soul-crystal.json — soul crystal contents on death
    Git tags: life-{NNN}-born, life-{NNN}-death

Falls back gracefully (logs a warning, no crash) when GITHUB_TOKEN is unset.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any

from github import Github, GithubException, UnknownObjectException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default repo (falls back to a diary/ subfolder in the same repo)
# ---------------------------------------------------------------------------
_DEFAULT_DIARY_REPO = "sagar0163/upaya-jivika"


# ---------------------------------------------------------------------------
# DiaryWriter
# ---------------------------------------------------------------------------

class DiaryWriter:
    """Writes markdown diary entries to a GitHub repo via PyGithub.

    If GITHUB_TOKEN is not set, all write methods become silent no-ops that
    log a warning — the survival loop never crashes because of a missing key.
    """

    def __init__(self, repo_name: str | None = None) -> None:
        self._day_counter: int = 0
        self._born_tagged: set[int] = set()

        token = os.environ.get("GITHUB_TOKEN")
        self._repo_name = repo_name or os.environ.get(
            "DIARY_REPO", _DEFAULT_DIARY_REPO
        )

        if not token:
            self._client: Github | None = None
            self._repo: Any = None
            logger.warning(
                "GITHUB_TOKEN not set — diary writes disabled (repo=%s)",
                self._repo_name,
            )
            return

        self._client = Github(token)
        try:
            self._repo = self._client.get_repo(self._repo_name)
            logger.info("Diary writer initialised: repo=%s", self._repo_name)
        except Exception:
            self._client = None
            self._repo = None
            logger.exception(
                "Failed to access diary repo %s — diary writes disabled",
                self._repo_name,
            )

    # -- public API ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._repo is not None

    def on_tick(
        self,
        life_number: int,
        debt: Decimal,
        state: str,
        events: list[str],
    ) -> None:
        """Write a daily diary entry and create the born tag if needed."""
        if not self.enabled:
            return

        self._day_counter += 1
        life_tag = self._life_tag(life_number)
        file_path = f"{life_tag}/day-{self._day_counter:02d}.md"

        content = self._render_daily(
            life_number=life_number,
            day=self._day_counter,
            debt=debt,
            state=state,
            events=events,
        )

        self._create_file(file_path, content, f"diary: {life_tag} day {self._day_counter}")

        if life_number not in self._born_tagged:
            self._create_tag(
                life_number,
                f"{life_tag}-born",
                f"Life {life_number} has begun",
            )
            self._born_tagged.add(life_number)

    def on_death(
        self,
        life_number: int,
        final_debt: Decimal,
        total_earned: Decimal,
        peak_state: str,
        best_platform: str,
        events: list[str],
        failed_strategies: list[str],
        key_lessons: list[str],
        avoid: list[str],
        soul_crystal: Any,
    ) -> None:
        """Write the death note, soul crystal JSON, and the death tag."""
        if not self.enabled:
            return

        life_tag = self._life_tag(life_number)

        # -- death-note.md -------------------------------------------------
        death_note = self._render_death_note(
            life_number=life_number,
            final_debt=final_debt,
            total_earned=total_earned,
            peak_state=peak_state,
            best_platform=best_platform,
            events=events,
            failed_strategies=failed_strategies,
            key_lessons=key_lessons,
            avoid=avoid,
        )
        self._create_file(
            f"{life_tag}/death-note.md",
            death_note,
            f"diary: {life_tag} death note",
        )

        # -- soul-crystal.json ---------------------------------------------
        crystal_dict: dict[str, Any]
        if isinstance(soul_crystal, BaseModel):
            crystal_dict = soul_crystal.model_dump(mode="json")
        elif isinstance(soul_crystal, dict):
            crystal_dict = soul_crystal
        else:
            crystal_dict = {}

        self._create_file(
            f"{life_tag}/soul-crystal.json",
            json.dumps(crystal_dict, indent=2, default=str),
            f"diary: {life_tag} soul crystal",
        )

        # -- death tag -----------------------------------------------------
        self._create_tag(
            life_number,
            f"{life_tag}-death",
            f"Life {life_number} has ended — debt ${final_debt}",
        )

    def reset_day_counter(self) -> None:
        """Reset the day counter for a new life."""
        self._day_counter = 0

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _life_tag(life_number: int) -> str:
        return f"life-{life_number:03d}"

    def _create_file(self, path: str, content: str, message: str) -> None:
        """Create or update a file in the diary repo."""
        try:
            self._repo.create_file(path, message, content)
        except GithubException as exc:
            if exc.status == 422:
                # File already exists — update it
                try:
                    contents = self._repo.get_contents(path)
                    self._repo.update_file(
                        path, message, content, contents.sha
                    )
                except Exception:
                    logger.exception("Failed to update %s", path)
            else:
                logger.exception("Failed to create %s", path)

    def _create_tag(
        self, life_number: int, tag_name: str, tag_message: str
    ) -> None:
        """Create a lightweight git tag if it does not already exist."""
        try:
            self._repo.get_git_ref(f"tags/{tag_name}")
        except UnknownObjectException:
            pass
        except Exception:
            logger.debug("Tag lookup failed for %s — creating", tag_name)
        else:
            return  # tag already exists

        try:
            sha = self._repo.get_branch("main").commit.sha
            self._repo.create_git_tag_and_release(
                tag_name, tag_message, tag_message, sha, "commit"
            )
        except Exception:
            logger.exception("Failed to create tag %s", tag_name)

    # -- markdown renderers ------------------------------------------------

    @staticmethod
    def _render_daily(
        life_number: int,
        day: int,
        debt: Decimal,
        state: str,
        events: list[str],
    ) -> str:
        """Render a daily diary entry as markdown."""
        lines = [
            f"# Life {life_number:03d} — Day {day}",
            "",
            f"**Debt:** ${debt}  ",
            f"**State:** {state}",
            "",
        ]

        if events:
            lines.append("## Today")
            lines.append("")
            for ev in events:
                lines.append(f"- {ev}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _render_death_note(
        life_number: int,
        final_debt: Decimal,
        total_earned: Decimal,
        peak_state: str,
        best_platform: str,
        events: list[str],
        failed_strategies: list[str],
        key_lessons: list[str],
        avoid: list[str],
    ) -> str:
        """Render a death note as markdown."""
        lines = [
            f"# Death Note — Life {life_number:03d}",
            "",
            f"**Final debt:** ${final_debt}  ",
            f"**Total earned:** ${total_earned}  ",
            f"**Peak state:** {peak_state}  ",
            f"**Best platform:** {best_platform or 'N/A'}",
            "",
        ]

        if events:
            lines.append("## Life events")
            lines.append("")
            for ev in events:
                lines.append(f"- {ev}")
            lines.append("")

        if failed_strategies:
            lines.append("## Failed strategies")
            lines.append("")
            for fail in failed_strategies:
                lines.append(f"- {fail}")
            lines.append("")

        if key_lessons:
            lines.append("## Key lessons")
            lines.append("")
            for lesson in key_lessons:
                lines.append(f"- {lesson}")
            lines.append("")

        if avoid:
            lines.append("## Avoid next life")
            lines.append("")
            for item in avoid:
                lines.append(f"- {item}")
            lines.append("")

        return "\n".join(lines)
