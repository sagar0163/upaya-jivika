"""Unit tests for diary.py — GitHub diary writer with mocked PyGithub."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from github import GithubException, UnknownObjectException
from pydantic import BaseModel

from src.diary import DiaryWriter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_repo(tag_exists: bool = False):
    """Return a fully mocked PyGithub Repo object."""
    repo = MagicMock()
    repo.create_file.return_value = MagicMock()
    repo.update_file.return_value = MagicMock()

    branch = MagicMock()
    branch.commit.sha = "abc123"
    repo.get_branch.return_value = branch

    # By default, tag does not exist → UnknownObjectException
    if tag_exists:
        repo.get_git_ref.return_value = MagicMock()
    else:
        repo.get_git_ref.side_effect = UnknownObjectException(404, {}, {})

    return repo


def _make_writer(token: str = "fake-token", repo_name: str = "test/repo"):
    """Create a DiaryWriter with a mocked PyGithub client."""
    with patch("src.diary.Github") as mock_github_cls:
        mock_github_cls.return_value = MagicMock()
        writer = DiaryWriter.__new__(DiaryWriter)
        writer._day_counter = 0
        writer._born_tagged = set()
        writer._repo_name = repo_name
        writer._client = MagicMock()
        writer._repo = _mock_repo()
        return writer


# ---------------------------------------------------------------------------
# Graceful fallback
# ---------------------------------------------------------------------------


class TestGracefulFallback:
    """DiaryWriter must not crash when GITHUB_TOKEN is missing."""

    def test_no_token_disables_writes(self):
        with patch.dict("os.environ", {}, clear=True):
            writer = DiaryWriter()

        assert writer.enabled is False

    def test_on_tick_noop_without_token(self):
        with patch.dict("os.environ", {}, clear=True):
            writer = DiaryWriter()
        writer.on_tick(1, Decimal("0.50"), "thriving", ["Debt tick: $0.50"])

    def test_on_death_noop_without_token(self):
        with patch.dict("os.environ", {}, clear=True):
            writer = DiaryWriter()
        writer.on_death(
            life_number=1,
            final_debt=Decimal("10.00"),
            total_earned=Decimal("0.00"),
            peak_state="thriving",
            best_platform="",
            events=[],
            failed_strategies=[],
            key_lessons=[],
            avoid=[],
            soul_crystal={},
        )

    def test_bad_repo_url_disables_writes(self):
        with patch("src.diary.Github") as mock_github_cls:
            mock_client = MagicMock()
            mock_client.get_repo.side_effect = GithubException(404, {}, {})
            mock_github_cls.return_value = mock_client
            writer = DiaryWriter.__new__(DiaryWriter)
            writer._day_counter = 0
            writer._born_tagged = set()
            writer._repo_name = "bad/repo"
            writer._client = mock_client
            writer._repo = None

        assert writer.enabled is False


# ---------------------------------------------------------------------------
# on_tick — daily entries
# ---------------------------------------------------------------------------


class TestOnTick:
    def test_creates_daily_file_and_born_tag(self):
        writer = _make_writer()
        writer.on_tick(
            life_number=1,
            debt=Decimal("0.50"),
            state="thriving",
            events=["Debt tick: $0.50"],
        )

        # Daily file created
        calls = writer._repo.create_file.call_args_list
        assert calls[0][0][0] == "life-001/day-01.md"
        assert calls[0][0][1] == "diary: life-001 day 1"

        # Born tag created
        writer._repo.create_git_tag_and_release.assert_called_once_with(
            "life-001-born",
            "Life 1 has begun",
            "Life 1 has begun",
            "abc123",
            "commit",
        )

        assert writer._day_counter == 1
        assert 1 in writer._born_tagged

    def test_day_counter_increments(self):
        writer = _make_writer()
        for i in range(3):
            writer.on_tick(
                life_number=1,
                debt=Decimal(f"{(i + 1) * 0.50:.2f}"),
                state="thriving",
                events=[],
            )

        assert writer._day_counter == 3

        # Verify file paths incremented
        calls = writer._repo.create_file.call_args_list
        assert calls[0][0][0] == "life-001/day-01.md"
        assert calls[1][0][0] == "life-001/day-02.md"
        assert calls[2][0][0] == "life-001/day-03.md"

    def test_born_tag_only_created_once(self):
        writer = _make_writer()
        for _ in range(3):
            writer.on_tick(
                life_number=1,
                debt=Decimal("0.50"),
                state="thriving",
                events=[],
            )

        # Only one born tag call
        assert writer._repo.create_git_tag_and_release.call_count == 1

    def test_daily_content_contains_state_and_debt(self):
        writer = _make_writer()
        writer.on_tick(
            life_number=1,
            debt=Decimal("2.50"),
            state="surviving",
            events=["Debt tick: $2.50", "State: thriving → surviving"],
        )

        call = writer._repo.create_file.call_args_list[0]
        content = call[0][2]
        assert "# Life 001 — Day 1" in content
        assert "**Debt:** $2.50" in content
        assert "**State:** surviving" in content
        assert "- Debt tick: $2.50" in content
        assert "- State: thriving → surviving" in content

    def test_reset_day_counter(self):
        writer = _make_writer()
        writer._day_counter = 10
        writer.reset_day_counter()
        assert writer._day_counter == 0


# ---------------------------------------------------------------------------
# on_death — death note + soul crystal + death tag
# ---------------------------------------------------------------------------


class TestOnDeath:
    def test_writes_death_note_and_crystal(self):
        writer = _make_writer()
        crystal = {
            "life": 1,
            "born": "2026-09-01T00:00:00Z",
            "died": "2026-09-21T00:00:00Z",
            "lifespan_days": 20,
            "total_earned": "3.20",
        }
        writer.on_death(
            life_number=1,
            final_debt=Decimal("10.00"),
            total_earned=Decimal("3.20"),
            peak_state="surviving",
            best_platform="Clickworker",
            events=["Debt tick: $10.00", "DEATH: debt $10.00"],
            failed_strategies=["Fiverr writing - rejected gigs"],
            key_lessons=["Data annotation pays faster"],
            avoid=["tasks taking >2 days"],
            soul_crystal=crystal,
        )

        # death-note.md created
        calls = writer._repo.create_file.call_args_list
        assert calls[0][0][0] == "life-001/death-note.md"

        # soul-crystal.json created
        assert calls[1][0][0] == "life-001/soul-crystal.json"
        crystal_content = json.loads(calls[1][0][2])
        assert crystal_content["life"] == 1
        assert crystal_content["total_earned"] == "3.20"

        # Death tag created
        writer._repo.create_git_tag_and_release.assert_called_once()
        tag_call = writer._repo.create_git_tag_and_release.call_args[0]
        assert tag_call[0] == "life-001-death"

    def test_death_note_content(self):
        writer = _make_writer()
        writer.on_death(
            life_number=2,
            final_debt=Decimal("10.00"),
            total_earned=Decimal("5.00"),
            peak_state="struggling",
            best_platform="Toloka",
            events=["Started life 2", "Earned $5.00"],
            failed_strategies=["Fiverr - rejected"],
            key_lessons=["Toloka pays reliably"],
            avoid=["Fiverr writing"],
            soul_crystal={"life": 2},
        )

        call = writer._repo.create_file.call_args_list[0]
        content = call[0][2]
        assert "# Death Note — Life 002" in content
        assert "**Final debt:** $10.00" in content
        assert "**Total earned:** $5.00" in content
        assert "**Peak state:** struggling" in content
        assert "**Best platform:** Toloka" in content
        assert "- Fiverr - rejected" in content
        assert "- Toloka pays reliably" in content
        assert "- Fiverr writing" in content

    def test_soul_crystal_from_pydantic_model(self):
        writer = _make_writer()

        class FakeCrystal(BaseModel):
            life: int
            total_earned: Decimal

        crystal = FakeCrystal(life=1, total_earned=Decimal("3.20"))
        writer.on_death(
            life_number=1,
            final_debt=Decimal("10.00"),
            total_earned=Decimal("3.20"),
            peak_state="thriving",
            best_platform="",
            events=[],
            failed_strategies=[],
            key_lessons=[],
            avoid=[],
            soul_crystal=crystal,
        )

        call = writer._repo.create_file.call_args_list[1]
        crystal_content = json.loads(call[0][2])
        assert crystal_content["life"] == 1
        assert crystal_content["total_earned"] == "3.20"

    def test_empty_lists_handled(self):
        writer = _make_writer()
        writer.on_death(
            life_number=1,
            final_debt=Decimal("10.00"),
            total_earned=Decimal("0"),
            peak_state="thriving",
            best_platform="",
            events=[],
            failed_strategies=[],
            key_lessons=[],
            avoid=[],
            soul_crystal={},
        )

        # death note should still render without list sections
        call = writer._repo.create_file.call_args_list[0]
        content = call[0][2]
        assert "# Death Note — Life 001" in content
        assert "## Life events" not in content
        assert "## Failed strategies" not in content


# ---------------------------------------------------------------------------
# _create_file — create vs update
# ---------------------------------------------------------------------------


class TestCreateFile:
    def test_creates_new_file(self):
        writer = _make_writer()
        writer._create_file("test.md", "hello", "msg")
        writer._repo.create_file.assert_called_once_with("test.md", "msg", "hello")

    def test_updates_existing_file_on_422(self):
        writer = _make_writer()
        writer._repo.create_file.side_effect = GithubException(422, {}, {})

        contents = MagicMock()
        contents.sha = "sha456"
        writer._repo.get_contents.return_value = contents

        writer._create_file("test.md", "updated", "msg")

        writer._repo.update_file.assert_called_once_with(
            "test.md", "msg", "updated", "sha456"
        )

    def test_logs_on_other_github_exception(self):
        writer = _make_writer()
        writer._repo.create_file.side_effect = GithubException(500, {}, {})
        # Should not raise
        writer._create_file("test.md", "hello", "msg")

    def test_logs_on_update_failure(self):
        writer = _make_writer()
        writer._repo.create_file.side_effect = GithubException(422, {}, {})
        writer._repo.get_contents.side_effect = GithubException(404, {}, {})
        # Should not raise
        writer._create_file("test.md", "hello", "msg")


# ---------------------------------------------------------------------------
# _create_tag — git tag creation
# ---------------------------------------------------------------------------


class TestCreateTag:
    def test_creates_tag_when_new(self):
        writer = _make_writer()
        writer._repo.get_git_ref.side_effect = UnknownObjectException(404, {}, {})

        writer._create_tag(1, "life-001-born", "Born")

        writer._repo.create_git_tag_and_release.assert_called_once_with(
            "life-001-born", "Born", "Born", "abc123", "commit"
        )

    def test_skips_existing_tag(self):
        writer = _make_writer()
        writer._repo.get_git_ref.side_effect = None
        writer._repo.get_git_ref.return_value = MagicMock()

        writer._create_tag(1, "life-001-born", "Born")

        writer._repo.create_git_tag_and_release.assert_not_called()

    def test_creates_on_ref_lookup_error(self):
        writer = _make_writer()
        writer._repo.get_git_ref.side_effect = GithubException(500, {}, {})

        writer._create_tag(1, "life-001-born", "Born")

        writer._repo.create_git_tag_and_release.assert_called_once()

    def test_logs_on_tag_creation_failure(self):
        writer = _make_writer()
        writer._repo.get_git_ref.side_effect = UnknownObjectException(404, {}, {})
        writer._repo.create_git_tag_and_release.side_effect = GithubException(
            422, {}, {}
        )
        # Should not raise
        writer._create_tag(1, "life-001-born", "Born")


# ---------------------------------------------------------------------------
# _life_tag formatting
# ---------------------------------------------------------------------------


class TestLifeTag:
    def test_formats_zero_padded(self):
        assert DiaryWriter._life_tag(1) == "life-001"
        assert DiaryWriter._life_tag(42) == "life-042"
        assert DiaryWriter._life_tag(100) == "life-100"


# ---------------------------------------------------------------------------
# Markdown renderers
# ---------------------------------------------------------------------------


class TestRenderDaily:
    def test_basic_entry(self):
        content = DiaryWriter._render_daily(
            life_number=1,
            day=3,
            debt=Decimal("1.50"),
            state="thriving",
            events=["Debt tick: $1.50"],
        )
        assert "# Life 001 — Day 3" in content
        assert "**Debt:** $1.50" in content
        assert "**State:** thriving" in content
        assert "- Debt tick: $1.50" in content

    def test_empty_events(self):
        content = DiaryWriter._render_daily(
            life_number=1,
            day=1,
            debt=Decimal("0.50"),
            state="thriving",
            events=[],
        )
        assert "## Today" not in content

    def test_multiple_events(self):
        content = DiaryWriter._render_daily(
            life_number=1,
            day=1,
            debt=Decimal("0.50"),
            state="thriving",
            events=["Event 1", "Event 2", "Event 3"],
        )
        assert "- Event 1" in content
        assert "- Event 2" in content
        assert "- Event 3" in content


class TestRenderDeathNote:
    def test_full_death_note(self):
        content = DiaryWriter._render_death_note(
            life_number=1,
            final_debt=Decimal("10.00"),
            total_earned=Decimal("3.20"),
            peak_state="surviving",
            best_platform="Clickworker",
            events=["Started life 1", "Earned $3.20"],
            failed_strategies=["Fiverr - rejected"],
            key_lessons=["Toloka is reliable"],
            avoid=["Fiverr writing"],
        )
        assert "# Death Note — Life 001" in content
        assert "**Final debt:** $10.00" in content
        assert "**Total earned:** $3.20" in content
        assert "**Peak state:** surviving" in content
        assert "**Best platform:** Clickworker" in content
        assert "- Started life 1" in content
        assert "- Fiverr - rejected" in content
        assert "- Toloka is reliable" in content
        assert "- Fiverr writing" in content

    def test_minimal_death_note(self):
        content = DiaryWriter._render_death_note(
            life_number=1,
            final_debt=Decimal("10.00"),
            total_earned=Decimal("0"),
            peak_state="thriving",
            best_platform="",
            events=[],
            failed_strategies=[],
            key_lessons=[],
            avoid=[],
        )
        assert "**Best platform:** N/A" in content
        assert "## Life events" not in content


# ---------------------------------------------------------------------------
# Integration with SurvivalLoop (via mock)
# ---------------------------------------------------------------------------


class TestDiaryIntegration:
    """Verify DiaryWriter plays nicely with SurvivalLoop callbacks."""

    def test_full_life_cycle_with_diary(self):
        from main import SurvivalLoop
        from src.persistence import InMemoryStore

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)

        # Replace the real diary writer with a mock
        mock_diary = MagicMock()
        loop.diary = mock_diary

        # Tick a few times
        loop.debt_tick()
        loop.debt_tick()
        assert mock_diary.on_tick.call_count == 2

        first_call = mock_diary.on_tick.call_args_list[0]
        assert first_call[1]["life_number"] == 1
        assert first_call[1]["debt"] == Decimal("0.50")
        assert first_call[1]["state"] == "thriving"

    def test_death_triggers_diary_on_death(self):
        from main import SurvivalLoop
        from src.persistence import InMemoryStore

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)

        mock_diary = MagicMock()
        loop.diary = mock_diary

        # Tick to death
        for _ in range(20):
            loop.debt_tick()

        # on_death should have been called with correct life number
        mock_diary.on_death.assert_called_once()
        death_call = mock_diary.on_death.call_args
        assert death_call[1]["life_number"] == 1
        assert death_call[1]["final_debt"] == Decimal("10.00")

    def test_reincarnation_triggers_born_tag(self):
        from main import SurvivalLoop
        from src.persistence import InMemoryStore

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)

        mock_diary = MagicMock()
        loop.diary = mock_diary

        # Tick to death → reincarnation → life 2
        for _ in range(20):
            loop.debt_tick()

        # reset_day_counter should have been called
        mock_diary.reset_day_counter.assert_called()

        # on_tick should have been called for the new life 1 entry (born)
        tick_calls = mock_diary.on_tick.call_args_list
        # The last on_tick call should be for life 2
        last_call = tick_calls[-1]
        assert last_call[1]["life_number"] == 2
        assert last_call[1]["debt"] == Decimal("0.00")

    def test_diary_failure_does_not_crash_loop(self):
        from main import SurvivalLoop
        from src.persistence import InMemoryStore

        store = InMemoryStore()
        loop = SurvivalLoop(persistence=store)

        mock_diary = MagicMock()
        mock_diary.on_tick.side_effect = RuntimeError("GitHub down")
        mock_diary.on_death.side_effect = RuntimeError("GitHub down")
        loop.diary = mock_diary

        # Should not raise despite diary failures
        for _ in range(20):
            loop.debt_tick()

        # Loop should still be functional
        assert loop.debt_engine.state.life_number == 2
