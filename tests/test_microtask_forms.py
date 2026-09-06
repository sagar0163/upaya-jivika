"""Unit tests for src/microtask_forms.py against recorded Clickworker forms.

Every step of the issue #62 automation chain's *form filling* leg is exercised
on realistic Clickworker HTML (recorded platform structure, sanitized):
detection, deterministic answering, filling, submission and confirmation.
"""

from decimal import Decimal

import pytest

from src.microtask_forms import (
    FieldKind,
    FormControl,
    answer,
    confirm_submission,
    detect_controls,
    fill_form,
    submit_form,
)

pytest.importorskip("tests.form_fixture")

from tests.form_fixture import (
    CLICKWORKER_EMPTY_PAGE_HTML,
    CLICKWORKER_TASK_FORM_HTML,
    CLICKWORKER_TASK_SUCCESS_HTML,
    build_page,
    instant_pacing,
)


class TestDetectControls:
    @pytest.mark.asyncio
    async def test_discovers_all_form_control_kinds(self):
        page = build_page(CLICKWORKER_TASK_FORM_HTML)
        controls = await detect_controls(page)

        kinds = {c.kind for c in controls}
        assert FieldKind.TEXTAREA in kinds
        assert FieldKind.SELECT in kinds
        assert FieldKind.RADIO in kinds
        assert FieldKind.CHECKBOX in kinds
        assert FieldKind.RATING in kinds
        assert FieldKind.TEXT in kinds

    @pytest.mark.asyncio
    async def test_radio_group_has_choices(self):
        page = build_page(CLICKWORKER_TASK_FORM_HTML)
        controls = await detect_controls(page)
        radio = next(c for c in controls if c.kind is FieldKind.RADIO)
        assert radio.name == "factuality"
        assert set(radio.options) == {"yes", "no"}

    @pytest.mark.asyncio
    async def test_select_has_choice_values(self):
        page = build_page(CLICKWORKER_TASK_FORM_HTML)
        controls = await detect_controls(page)
        select = next(c for c in controls if c.kind is FieldKind.SELECT)
        assert select.name == "category"
        assert "tech" in select.options
        assert "research" in select.options

    @pytest.mark.asyncio
    async def test_rating_has_scale_options(self):
        page = build_page(CLICKWORKER_TASK_FORM_HTML)
        controls = await detect_controls(page)
        rating = next(c for c in controls if c.kind is FieldKind.RATING)
        assert rating.options == ["1", "2", "3", "4", "5"]

    @pytest.mark.asyncio
    async def test_empty_page_has_no_controls(self):
        page = build_page(CLICKWORKER_EMPTY_PAGE_HTML)
        assert await detect_controls(page) == []


class TestAnswerGeneration:
    def test_deterministic_for_same_seed(self):
        control = FormControl(kind=FieldKind.TEXT, name="summary", label="", selector="", index=0)
        assert answer(control, seed="Factuality Assessment") == answer(control, seed="Factuality Assessment")

    def test_explicit_field_answer_wins(self):
        control = FormControl(
            kind=FieldKind.RADIO,
            name="factuality",
            label="",
            selector="",
            index=0,
            options=["yes", "no"],
        )
        assert answer(control, seed="s", explicit={"factuality": "no"}) == "no"

    def test_explicit_catch_all_wins_for_free_text(self):
        control = FormControl(kind=FieldKind.TEXT, name="summary", label="", selector="", index=0)
        assert answer(control, seed="s", explicit={"*": "The statement is self-evident."}) == (
            "The statement is self-evident."
        )

    def test_checkbox_terms_auto_checked(self):
        control = FormControl(kind=FieldKind.CHECKBOX, name="agree_terms", label="", selector="", index=0)
        assert answer(control, seed="s") is True

    def test_checkbox_non_terms_left_unchecked(self):
        control = FormControl(kind=FieldKind.CHECKBOX, name="notify_me", label="", selector="", index=0)
        assert answer(control, seed="s") is False

    def test_rating_and_select_always_in_bounds(self):
        rating = FormControl(
            kind=FieldKind.RATING,
            name="",
            label="",
            selector="",
            index=0,
            options=["1", "2", "3", "4", "5"],
        )
        select = FormControl(
            kind=FieldKind.SELECT,
            name="category",
            label="",
            selector="",
            index=0,
            options=["tech", "research", "marketing"],
        )
        assert answer(rating, seed="x") in rating.options
        assert answer(select, seed="x") in select.options


class TestFillForm:
    @pytest.mark.asyncio
    async def test_fills_every_detected_control(self):
        page = build_page(
            CLICKWORKER_TASK_FORM_HTML,
            screens={"success": CLICKWORKER_TASK_SUCCESS_HTML},
        )
        with instant_pacing():
            report = await fill_form(page, seed="Factuality Assessment")

        actions = {r["action"] for r in report}
        assert actions >= {"fill", "select", "check", "click"}
        # The terms checkbox is auto-checked; radio + rating both check/click.
        by_kind = {r["kind"]: r for r in report}
        assert by_kind[FieldKind.TEXT.value]["value"]
        assert by_kind[FieldKind.TEXTAREA.value]["value"]
        assert by_kind[FieldKind.SELECT.value]["value"] in {"tech", "research", "marketing"}
        assert by_kind[FieldKind.RADIO.value]["value"] in {"yes", "no"}
        assert by_kind[FieldKind.RATING.value]["value"] in {"1", "2", "3", "4", "5"}
        assert by_kind[FieldKind.CHECKBOX.value]["action"] == "check"

    @pytest.mark.asyncio
    async def test_fill_is_deterministic(self):
        page = build_page(CLICKWORKER_TASK_FORM_HTML)
        with instant_pacing():
            report_a = await fill_form(page, seed="Factuality Assessment")
        page = build_page(CLICKWORKER_TASK_FORM_HTML)
        with instant_pacing():
            report_b = await fill_form(page, seed="Factuality Assessment")
        assert report_a == report_b

    @pytest.mark.asyncio
    async def test_explicit_answers_land_in_the_form(self):
        page = build_page(CLICKWORKER_TASK_FORM_HTML)
        with instant_pacing():
            report = await fill_form(
                page,
                seed="Factuality Assessment",
                explicit={"factuality": "no", "category": "research"},
            )
        by_kind = {r["kind"]: r for r in report}
        assert by_kind[FieldKind.RADIO.value]["value"] == "no"
        assert by_kind[FieldKind.SELECT.value]["value"] == "research"

    @pytest.mark.asyncio
    async def test_empty_page_fills_nothing(self):
        page = build_page(CLICKWORKER_EMPTY_PAGE_HTML)
        with instant_pacing():
            report = await fill_form(page, seed="anything")
        assert report == []


class TestSubmitAndConfirm:
    @pytest.mark.asyncio
    async def test_submit_found_on_recorded_form(self):
        page = build_page(CLICKWORKER_TASK_FORM_HTML)
        assert await submit_form(page) is True

    @pytest.mark.asyncio
    async def test_submit_missing_on_informational_page(self):
        page = build_page(CLICKWORKER_EMPTY_PAGE_HTML)
        assert await submit_form(page) is False

    @pytest.mark.asyncio
    async def test_confirmation_marker_detected_after_submit(self):
        page = build_page(
            CLICKWORKER_TASK_FORM_HTML,
            screens={"success": CLICKWORKER_TASK_SUCCESS_HTML},
        )
        markers = "[class*='success'], [class*='thank'], .alert-success, [class*='completed']"
        assert await confirm_submission(page, markers) is False
        await submit_form(page)
        assert await confirm_submission(page, markers) is True

    @pytest.mark.asyncio
    async def test_confirm_returns_false_without_marker(self):
        page = build_page(CLICKWORKER_EMPTY_PAGE_HTML)
        assert await confirm_submission(page, "[class*='success'], .alert-success") is False


class TestPricingHelpers:
    def test_reward_parsing_contract(self):
        from src.task_executor import ClickworkerConnector

        assert ClickworkerConnector._to_decimal("Reward: $5.00") == Decimal("5.00")
        assert ClickworkerConnector._to_decimal("Reward: $7.50") == Decimal("7.50")
        assert ClickworkerConnector._to_decimal("N/A") == Decimal("0")
