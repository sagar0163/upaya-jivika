"""Deterministic microtask form automation (issue #62 — Clickworker focus).

Clickworker microtask forms are plain HTML forms: text/number inputs,
``<textarea>``s, ``<select>`` dropdowns, radio groups, checkboxes and
rating-star buttons. This module is the *form filling / automation* leg of
the single-platform automation chain. It provides a small, framework-free
engine that:

- detects the controls present on a task page (a real browser session or a
  recorded HTML fixture — mirroring artifact.md §9's "mocked/recorded
  responses in tests, never live scraping")
- produces a *deterministic*, human-plausible answer for every control,
  seeded from the task, so the same task always gets the same answers
  (stable across runs and auditable via the returned fill report)
- fills the form at a human pace and submits it
- confirms the submission via success markers

Explicit model answers can override the generated ones through
``explicit={field_name: value}`` (e.g. a task the agent can genuinely
answer), and ``explicit["*"]`` is the catch-all for free-text fields.

.. note::
   Only a tiny Playwright-shaped surface is required from ``page``
   (``locator().count()/nth()/first``, ``locator().locator()``,
   ``inner_text()``, ``get_attribute()``,
   ``fill()/select_option()/check()/uncheck()/click()``) so the same engine
   drives the real browser in production and recorded HTML in tests.
   Hard-blacklisted tasks never reach this engine (``ethical_guardrail``
   filters them in ``task_scorer`` and ``task_executor``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Human-paced gap between individual form interactions so an automated run
# never hammers a live platform (artifact.md §15).
HUMAN_PAUSE_SECONDS = 0.3

# Absolute safety cap on how many controls one page may contribute.
MAX_CONTROLS = 50

# Controls whose name/label signals an acceptance/consent checkbox. Only
# these get auto-checked; anything else is left untouched out of caution.
TERMS_PATTERN = re.compile(
    r"(agree|accept|confirm|consent|terms|opt-?in|i am (over )?18|"
    r"18 or older|the answers are (my|solely my) own)",
    re.IGNORECASE,
)

# Dropdown options that are just "please choose"-style placeholders — the
# engine never selects one of these as an answer.
_PLACEHOLDER_WORDS = {
    "select",
    "please select",
    "please choose",
    "choose",
    "choose one",
    "select one",
    "pick one",
    "pick",
    "bitte wählen",
    "auswählen",
    "suchen",
    "empty",
    "none",
    "",
}


def _is_placeholder_option(text: str) -> bool:
    t = re.sub(r"[\s\-—_]+", " ", text.lower()).strip()
    if t in _PLACEHOLDER_WORDS:
        return True
    return t.startswith(("select ", "choose ", "pick ", "please ")) and len(t) < 24


class FieldKind(str, Enum):
    """Kinds of form control the engine can drive."""

    """Kinds of form control the engine can drive."""

    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    EMAIL = "email"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    RATING = "rating"


# CSS selectors used to detect each control kind. Order matters: ``index``
# on a detected control refers to its ``nth()`` position inside this selector
# on the page, so re-locating during the fill step is exact.
CONTROL_SELECTORS: dict[FieldKind, str] = {
    FieldKind.TEXT: (
        "input[type='text'], input[type='search'], input[type='tel'], input[type='url'], input:not([type])"
    ),
    FieldKind.TEXTAREA: "textarea",
    FieldKind.NUMBER: "input[type='number'], input[type='range']",
    FieldKind.EMAIL: "input[type='email']",
    FieldKind.SELECT: "select",
    FieldKind.RADIO: "input[type='radio']",
    FieldKind.CHECKBOX: "input[type='checkbox']",
    FieldKind.RATING: "[class*='rating'], [class*='stars']",
}

# Selectors tried, in order, when looking for a submit control.
SUBMIT_SELECTORS = (
    "form button[type='submit']",
    "button[type='submit']",
    "input[type='submit']",
    "button[name='submit']",
)

# Star/scale buttons inside a rating container, in DOM order (value 1..n).
RATING_CHOICE_SELECTOR = "button, [class*='star'], [class*='scale']"


@dataclass
class FormControl:
    """A single detected form control on the task page."""

    kind: FieldKind
    name: str
    label: str
    selector: str
    index: int
    options: list[str] = field(default_factory=list)
    # Only populated for radio/select/rating controls that carry choices.
    title: str = ""


# Word bank for deterministic free-text answers. Short and neutral — this is
# scaffolding for fields the agent cannot semantically answer yet; tasks the
# agent *can* answer pass their answer in via ``explicit``.
_PHRASE_WORDS = (
    "accurate",
    "complete",
    "relevant",
    "clear",
    "concise",
    "verified",
    "consistent",
    "structured",
    "specific",
    "neutral",
)


def _digest(seed: str, key: str) -> int:
    """Return a stable pseudo-random integer for ``(seed, key)``."""
    return int(hashlib.sha256(f"{seed}|{key}".encode("utf-8")).hexdigest(), 16)


def answer(control: FormControl, seed: str, explicit: Optional[dict] = None) -> Any:
    """Return the deterministic answer to fill into ``control``.

    ``explicit`` (a ``{field_name_or_label_or_kind: value}`` map, plus the
    ``"*"`` catch-all) wins over the generated answer whenever it names the
    control. Otherwise a seeded, stable value is produced per control kind.
    """
    if explicit:
        keys = (control.name, control.label, control.kind.value, "*")
        for key in keys:
            if key and key in explicit:
                return explicit[key]

    h = _digest(seed, f"{control.kind.value}|{control.name}|{control.label}|{control.index}")

    if control.kind is FieldKind.TEXT:
        w1 = _PHRASE_WORDS[h % len(_PHRASE_WORDS)]
        w2 = _PHRASE_WORDS[(h >> 5) % len(_PHRASE_WORDS)]
        return f"Completed: {w1} {w2}."
    if control.kind is FieldKind.TEXTAREA:
        w1 = _PHRASE_WORDS[h % len(_PHRASE_WORDS)]
        w2 = _PHRASE_WORDS[(h >> 5) % len(_PHRASE_WORDS)]
        return f"Completed. This response is {w1} and {w2} per the task instructions."
    if control.kind is FieldKind.NUMBER:
        return str(1 + h % 10)
    if control.kind is FieldKind.EMAIL:
        return f"worker.{h % 100000}@example.com"
    if control.kind is FieldKind.SELECT:
        options = control.options or ["1"]
        return options[h % len(options)]
    if control.kind is FieldKind.RADIO:
        options = control.options or ["yes"]
        return options[h % len(options)]
    if control.kind is FieldKind.RATING:
        options = control.options or ["1", "2", "3", "4", "5"]
        return options[h % len(options)]
    if control.kind is FieldKind.CHECKBOX:
        return bool(TERMS_PATTERN.search(f"{control.name} {control.label}"))
    return None


async def detect_controls(page: Any) -> list[FormControl]:
    """Detect all fillable controls on ``page``.

    ``page`` only needs the small Playwright-shaped surface described in the
    module docstring. Radio buttons are returned as one control per group
    (``options`` = the choice values, ``index`` = the first radio's ``nth()``
    position); rating widgets as one control per container (``options`` =
    ``"1" .. "n"`` for its star buttons).
    """
    controls: list[FormControl] = []

    async def _name_of(el: Any, fallback_attr: str = "") -> str:
        try:
            name = await el.get_attribute("name")
            name = (name or "").strip()
            if not name and fallback_attr:
                name = (await el.get_attribute(fallback_attr) or "").strip()
            return name if isinstance(name, str) else ""
        except Exception:
            return ""

    async def _id_of(el: Any) -> str:
        try:
            id_ = await el.get_attribute("id")
            return (id_ or "").strip() if isinstance(id_, str) else ""
        except Exception:
            return ""

    async def _label_text(name: str, id_: str) -> str:
        for ref in (name, id_):
            if not ref:
                continue
            try:
                loc = page.locator(f"label[for='{ref}']")
                if await loc.count() > 0:
                    return (await loc.first.inner_text()).strip()
            except Exception:
                continue
        return ""

    def _maybe_add(control: Optional[FormControl]) -> None:
        if control is not None and len(controls) < MAX_CONTROLS:
            controls.append(control)

    # Simple single-value inputs: text, textarea, number, email.
    for kind in (FieldKind.TEXT, FieldKind.TEXTAREA, FieldKind.NUMBER, FieldKind.EMAIL):
        selector = CONTROL_SELECTORS[kind]
        count = await page.locator(selector).count()
        for i in range(count):
            el = page.locator(selector).nth(i)
            name = await _name_of(el, "id")
            id_ = await _id_of(el)
            label = await _label_text(name, id_)
            _maybe_add(FormControl(kind=kind, name=name, label=label, selector=selector, index=i))

    # Dropdowns, with their options.
    selector = CONTROL_SELECTORS[FieldKind.SELECT]
    count = await page.locator(selector).count()
    for i in range(count):
        el = page.locator(selector).nth(i)
        name = await _name_of(el, "id")
        id_ = await _id_of(el)
        label = await _label_text(name, id_)
        options: list[str] = []
        option_loc = el.locator("option")
        opt_count = await option_loc.count()
        for oi in range(opt_count):
            opt = option_loc.nth(oi)
            value = await opt.get_attribute("value")
            value = (value or "").strip() if isinstance(value, str) else ""
            if not value:
                value = (await opt.inner_text()).strip()
            if value and not _is_placeholder_option(value):
                options.append(value)
        _maybe_add(
            FormControl(
                kind=FieldKind.SELECT,
                name=name,
                label=label,
                selector=selector,
                index=i,
                options=options,
            )
        )

    # Radio buttons, grouped by name so one answer fills the whole group.
    radio_selector = CONTROL_SELECTORS[FieldKind.RADIO]
    radio_count = await page.locator(radio_selector).count()
    groups: dict[str, dict[str, Any]] = {}
    for i in range(radio_count):
        el = page.locator(radio_selector).nth(i)
        name = await _name_of(el)
        value = await el.get_attribute("value")
        value = (value or "").strip() if isinstance(value, str) else ""
        if not name:
            continue
        group = groups.setdefault(name, {"index": i, "options": [], "id": ""})
        if value:
            group["options"].append(value)
    for name, group in groups.items():
        _maybe_add(
            FormControl(
                kind=FieldKind.RADIO,
                name=name,
                label=await _label_text(name, group["id"]),
                selector=radio_selector,
                index=group["index"],
                options=group["options"],
            )
        )

    # Checkboxes.
    checkbox_selector = CONTROL_SELECTORS[FieldKind.CHECKBOX]
    checkbox_count = await page.locator(checkbox_selector).count()
    for i in range(checkbox_count):
        el = page.locator(checkbox_selector).nth(i)
        name = await _name_of(el, "id")
        id_ = await _id_of(el)
        label = await _label_text(name, id_)
        _maybe_add(
            FormControl(
                kind=FieldKind.CHECKBOX,
                name=name,
                label=label,
                selector=checkbox_selector,
                index=i,
            )
        )

    # Rating/star widgets: one control per container, options = "1".."n".
    rating_selector = CONTROL_SELECTORS[FieldKind.RATING]
    rating_count = await page.locator(rating_selector).count()
    for i in range(rating_count):
        container = page.locator(rating_selector).nth(i)
        stars = container.locator(RATING_CHOICE_SELECTOR)
        star_count = await stars.count()
        if star_count < 2:
            continue
        title = ""
        try:
            title_block = await container.locator(
                "h3, h4, .title, [class*='question'], [class*='instruction']"
            ).first.inner_text()
            title = title_block.strip()
        except Exception:
            logger.debug("No title/label element found for rating control %d", i, exc_info=True)
        _maybe_add(
            FormControl(
                kind=FieldKind.RATING,
                name="",
                label="",
                selector=rating_selector,
                index=i,
                options=[str(k) for k in range(1, star_count + 1)],
                title=title,
            )
        )

    return controls


async def fill_form(
    page: Any,
    seed: str,
    explicit: Optional[dict] = None,
    human_pace: bool = True,
) -> list[dict[str, str]]:
    """Detect and fill every control on ``page``.

    Returns a fill report (one entry per acted-on control) that callers can
    attach to a task result for the audit trail. Always deterministic for a
    given ``seed``. Never raises on a single bad control — the rest of the
    form still gets filled.
    """
    report: list[dict[str, str]] = []
    controls = await detect_controls(page)

    for control in controls:
        value = answer(control, seed, explicit)
        if value is None:
            continue

        el = page.locator(control.selector).nth(control.index)
        action = ""
        try:
            if control.kind in (FieldKind.TEXT, FieldKind.TEXTAREA, FieldKind.NUMBER, FieldKind.EMAIL):
                await el.fill(str(value))
                action = "fill"
            elif control.kind is FieldKind.SELECT:
                await el.select_option(str(value))
                action = "select"
            elif control.kind is FieldKind.RADIO:
                offset = control.options.index(str(value)) if str(value) in control.options else 0
                await page.locator(CONTROL_SELECTORS[FieldKind.RADIO]).nth(control.index + offset).check()
                action = "check"
            elif control.kind is FieldKind.CHECKBOX:
                if value:
                    await el.check()
                    action = "check"
                else:
                    await el.uncheck()
                    action = "uncheck"
            elif control.kind is FieldKind.RATING:
                star_index = int(value) - 1 if str(value).isdigit() else 0
                group = page.locator(CONTROL_SELECTORS[FieldKind.RATING]).nth(control.index)
                await group.locator(RATING_CHOICE_SELECTOR).nth(star_index).click()
                action = "click"
            else:  # pragma: no cover - exhaustive enum
                continue

            report.append(
                {
                    "kind": control.kind.value,
                    "name": control.name,
                    "label": control.label,
                    "value": str(value),
                    "action": action,
                }
            )
        except Exception as exc:
            logger.debug(
                "Skipped control %s (%s) on %s: %s",
                control.name or control.index,
                control.kind.value,
                seed,
                exc,
            )
            continue

        if human_pace:
            await asyncio.sleep(HUMAN_PAUSE_SECONDS)

    return report


async def submit_form(page: Any) -> bool:
    """Click the first submit control found on ``page``.

    Returns True if a submit control existed and was clicked.
    """
    for selector in SUBMIT_SELECTORS:
        try:
            if await page.locator(selector).count() > 0:
                await page.locator(selector).first.click()
                return True
        except Exception as exc:
            logger.debug("Submit via %s failed: %s", selector, exc)
    return False


async def confirm_submission(page: Any, markers: str) -> bool:
    """Return True if any success marker appears on ``page`` after submit."""
    try:
        return await page.locator(markers).count() > 0
    except Exception:
        return False
