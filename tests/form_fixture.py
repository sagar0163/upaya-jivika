"""Fake Playwright page driven by *real recorded HTML* for Clickworker tests.

The Clickworker connectors are exercised against realistic job-listing and
microtask-form markup (issue #62: "unit test every step on real platform
forms"). ``lxml`` parses the HTML; a small CSS-subset → XPath translator finds
elements; ``FakePage``/``FakeContext`` present the connector with the same
Playwright-shaped API it uses against a real browser.

Only the CSS subset the connectors/engine actually use is translated: tags,
``#id``, ``.class``, ``[attr]``, ``[attr=v]``, ``[attr*=v]``, ``:not([attr])``,
descendant combinators and comma alternatives. Everything else is test-only;
a real browser remains the source of truth in production.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import contextmanager
from unittest.mock import AsyncMock

# lxml is only needed on the test side and is deliberately not added to
# requirements.txt. Skip the fixture module (and its tests) if it's missing.
_HTML_LIB = None
try:
    from lxml import html as lxml_html  # type: ignore[import-not-found]

    _HTML_LIB = lxml_html
except ImportError:  # pragma: no cover - environment-dependent
    _HTML_LIB = None


_PSEUDO_NOT = re.compile(r":not\(\[([^\]]+)\]\)")


def _qstr(value: str) -> str:
    value = str(value)
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{p}'" for p in parts) + ")"


def _attr_predicate(inner: str) -> str:
    inner = inner.strip()
    name, op, raw = inner.lstrip("@"), None, None
    for candidate in ("*=", "^=", "$=", "~=", "="):
        if candidate in inner:
            left, _, right = inner.partition(candidate)
            name, op, raw = left.strip().lstrip("@"), candidate, right.strip()
            break
    attr = f"@{name}"
    if op is None:
        return attr
    value = re.sub(r"""^["']|["']$""", "", raw)
    if op == "=":
        return f"{attr}={_qstr(value)}"
    if op == "*=":
        return f"contains({attr},{_qstr(value)})"
    if op == "^=":
        return f"starts-with({attr},{_qstr(value)})"
    if op == "$=":
        if not value:
            return f"string-length({attr})=0"
        return f"substring({attr}, string-length({attr}) - {len(value) - 1}) = {_qstr(value)}"
    if op == "~=":
        return f"contains(concat(' ', normalize-space({attr}), ' '), {_qstr(' ' + value + ' ')})"
    return attr


def _token_to_xpath(token: str) -> str:
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*|\*)?(.*)$", token, re.S)
    tag = match.group(1) or "*"
    rest = match.group(2)
    predicates: list[str] = []
    i, n = 0, len(rest)
    while i < n:
        ch = rest[i]
        if ch == "#":
            j = i + 1
            while j < n and (rest[j].isalnum() or rest[j] in "-_"):
                j += 1
            predicates.append(f"@id={_qstr(rest[i + 1 : j])}")
            i = j
        elif ch == ".":
            j = i + 1
            while j < n and (rest[j].isalnum() or rest[j] in "-_"):
                j += 1
            predicates.append(
                f"contains(concat(' ', normalize-space(@class), ' '), {_qstr(' ' + rest[i + 1 : j] + ' ')})"
            )
            i = j
        elif ch == "[":
            depth, j = 1, i + 1
            while j < n and depth:
                if rest[j] == "[":
                    depth += 1
                elif rest[j] == "]":
                    depth -= 1
                if depth:
                    j += 1
            predicates.append(_attr_predicate(rest[i + 1 : j]))
            i = j + 1
        elif ch == ":":
            found = _PSEUDO_NOT.match(rest, i)
            if found:
                predicates.append(f"not(@{found.group(1).lstrip('@')})")
                i += found.end()
            else:
                i += 1
        else:
            i += 1
    return tag + "".join(f"[{p}]" for p in predicates)


def _split_steps(branch: str) -> list[str]:
    steps: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in branch:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch.isspace() and depth == 0:
            if current:
                steps.append("".join(current).strip())
                current = []
        else:
            current.append(ch)
    if current:
        steps.append("".join(current).strip())
    return [s for s in steps if s]


def css_to_xpath(css: str, scoped: bool = False) -> str:
    """Translate the CSS subset the connectors use into XPath."""
    branches = [b.strip() for b in css.split(",") if b.strip()]
    expressions = []
    for branch in branches:
        steps = _split_steps(branch)
        expression = (".//" if scoped else "//") + _token_to_xpath(steps[0])
        for step in steps[1:]:
            expression += "//" + _token_to_xpath(step)
        expressions.append(f"({expression})")
    return " | ".join(expressions)


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _is_submit(node) -> bool:
    tag = (node.tag or "").lower()
    if tag not in ("button", "input"):
        return False
    return (node.get("type") or "").lower() == "submit" or (node.get("name") or "") == "submit"


class FakeLocator:
    """A locator over an lxml node-set with a Playwright-shaped API."""

    def __init__(self, page: "FakePage", nodes, css: str, scoped: bool = False):
        self.page = page
        self._nodes = list(nodes)
        self._css = css
        self._scoped = scoped

    @property
    def first(self) -> "FakeLocator":
        return FakeLocator(self.page, [self._nodes[0]] if self._nodes else [], self._css, self._scoped)

    def nth(self, index: int) -> "FakeLocator":
        if 0 <= index < len(self._nodes):
            return FakeLocator(self.page, [self._nodes[index]], self._css, self._scoped)
        return FakeLocator(self.page, [], self._css, self._scoped)

    def locator(self, css: str) -> "FakeLocator":
        nodes = []
        for node in self._nodes:
            nodes.extend(node.xpath(css_to_xpath(css, scoped=True)))
        return FakeLocator(self.page, nodes, css, scoped=True)

    async def count(self) -> int:
        return len(self._nodes)

    async def inner_text(self) -> str:
        for node in self._nodes:
            text = _norm(node.text_content())
            if text:
                return text
        return ""

    async def text_content(self) -> str:
        for node in self._nodes:
            text = _norm(node.text_content())
            if text:
                return text
        return ""

    async def get_attribute(self, name: str):
        for node in self._nodes:
            value = node.get(name)
            if value is not None:
                return value
        return None

    async def fill(self, value: str) -> None:
        for node in self._nodes:
            node.set("value", str(value))

    async def select_option(self, value: str) -> None:
        value = str(value)
        for node in self._nodes:
            for option in node.xpath(".//option"):
                if (option.get("value") or "") == value or _norm(option.text_content()) == value:
                    option.set("selected", "selected")
                    node.set("value", value)
                    return
            raise Exception(f"Option {value!r} not found for select")

    async def check(self) -> None:
        for node in self._nodes:
            node.set("checked", "checked")
            node.set("value", "on")

    async def uncheck(self) -> None:
        for node in self._nodes:
            node.set("checked", "")

    async def click(self) -> None:
        if self._nodes:
            self.page.on_click(self._nodes[0])

    async def is_visible(self) -> bool:
        for node in self._nodes:
            if node.get("hidden") is not None:
                return False
        return bool(self._nodes)

    async def all(self) -> list["FakeLocator"]:
        return [self.nth(i) for i in range(len(self._nodes))]


class FakePage:
    """A single fake browser page backed by a recorded HTML document."""

    def __init__(self, html: str, screens=None, url: str = "https://www.clickworker.com"):
        if _HTML_LIB is None:  # pragma: no cover
            raise RuntimeError("lxml is required for FakePage tests")
        self._root = _HTML_LIB.fromstring(html)
        self._screens = screens or {}
        self._success_html = self._screens.get("success")
        self.url = url
        self.name = "fake-page"

    async def set_extra_http_headers(self, headers: dict) -> None:
        return None

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.url = url
        await asyncio.sleep(0)
        return None

    async def wait_for_url(self, pattern: str, timeout: float | None = None) -> None:
        return None

    async def wait_for_selector(self, selector: str, timeout: float | None = None, state: str | None = None):
        locator = self.locator(selector)
        if await locator.count() == 0:
            raise TimeoutError(f"wait_for_selector timed out: {selector}")
        return locator.first

    async def hover(self, selector: str) -> None:
        return None

    async def click(self, selector: str) -> None:
        await self.locator(selector).first.click()

    async def fill(self, selector: str, value: str) -> None:
        await self.locator(selector).first.fill(value)

    async def type(self, selector: str, value: str, delay: float | None = None) -> None:
        await self.locator(selector).first.fill(value)

    async def inner_text(self, selector: str) -> str:
        return await self.locator(selector).inner_text()

    async def evaluate(self, script: str, arg=None) -> None:
        return None

    def locator(self, css: str) -> FakeLocator:
        nodes = self._root.xpath(css_to_xpath(css, scoped=False))
        return FakeLocator(self, nodes, css, scoped=False)

    def on_click(self, node) -> None:
        if node is None:
            return
        swap_target = node.get("data-swap")
        if swap_target and swap_target in self._screens:
            self._root = _HTML_LIB.fromstring(self._screens[swap_target])
            self.url = swap_target
            return
        if _is_submit(node) and self._success_html:
            self._root = _HTML_LIB.fromstring(self._success_html)
            self.url = self.url.rsplit("#", 1)[0] + "#submitted"


class FakeContext:
    """Fake ``BrowserContext`` that yields :class:`FakePage` instances."""

    def __init__(self, html: str = "", screens=None):
        self._html = html
        self._screens = screens or {}

    async def new_page(self) -> FakePage:
        page = FakePage(self._html, self._screens)
        return page


@contextmanager
def instant_pacing():
    """No-op ``asyncio.sleep`` for the duration of a FakePage-driven test.

    The connectors pace themselves with human-speed delays; a fake browser
    must not pay wall-clock for those. Patching is confined to the ``with``
    block — tests are sequential within their own coroutine, so nothing else
    on the loop observes the change.
    """
    original = asyncio.sleep
    asyncio.sleep = AsyncMock(return_value=None)  # type: ignore[assignment]
    try:
        yield
    finally:
        asyncio.sleep = original


# ---------------------------------------------------------------------------
# Recorded Clickworker HTML fixtures (real platform structure, sanitized)
# ---------------------------------------------------------------------------

CLICKWORKER_LOGIN_HTML = """
<html><body>
  <form class="login-form">
    <input name="email" type="email" value="" />
    <input name="password" type="password" value="" />
    <button name="submit" type="submit">Log in</button>
  </form>
</body></html>
"""

CLICKWORKER_DASHBOARD_HTML = """
<html><body>
  <nav class="main-nav"><a href="/dashboard">Dashboard</a></nav>
  <div class="dashboard">
    <span class="balance">Balance: €12.34</span>
    <span class="earnings today">Today: $1.20</span>
  </div>
</body></html>
"""

CLICKWORKER_JOBS_HTML = """
<html><body>
  <div class="job-list">
    <article class="job-card">
      <h3 class="job-title">Product Description Writing</h3>
      <p class="job-description">Write a short product description for an
        online shop (max. 100 words).</p>
      <span class="reward-cell">Reward: $5.00</span>
      <a class="job-link" href="/jobs/1234">Show details</a>
    </article>
    <article class="job-card">
      <h2 class="job-title">Factuality Assessment</h2>
      <p class="job-description">Evaluate whether short statements are
        factually correct.</p>
      <span class="reward-cell">Reward: $7.50</span>
      <a class="job-link" href="/jobs/9876">Show details</a>
      <span class="job-status">Open</span>
    </article>
    <li class="task-item">
      <h3 class="title">No reward shown</h3>
      <span class="reward-cell">N/A</span>
      <a href="/jobs/5555">Open</a>
    </li>
  </div>
</body></html>
"""

CLICKWORKER_TASK_FORM_HTML = """
<html><body>
  <form class="task-form">
    <h3 class="task-title">Factuality Assessment</h3>
    <p class="task-instruction">Judge the statement below, rate its quality,
      pick a category, confirm your answers and add a comment.</p>

    <div class="task-question">
      <p>The capital of France is Paris.</p>
      <label><input type="radio" name="factuality" value="yes" /> Yes</label>
      <label><input type="radio" name="factuality" value="no" /> No</label>
    </div>

    <div class="task-question">
      <span>Overall quality rating</span>
      <div class="rating-select">
        <button class="star" value="1" aria-label="1 star">1</button>
        <button class="star" value="2" aria-label="2 stars">2</button>
        <button class="star" value="3" aria-label="3 stars">3</button>
        <button class="star" value="4" aria-label="4 stars">4</button>
        <button class="star" value="5" aria-label="5 stars">5</button>
      </div>
    </div>

    <div class="task-question">
      <label for="category">Choose a category</label>
      <select id="category" name="category">
        <option value="">-- select --</option>
        <option value="tech">Technology</option>
        <option value="research">Research</option>
        <option value="marketing">Marketing</option>
      </select>
    </div>

    <div class="task-question">
      <label>In one sentence, summarise the statement</label>
      <input type="text" name="summary" />
    </div>

    <div class="task-question">
      <label><input type="checkbox" name="agree_terms" value="yes" />
        I confirm my answers are my own work</label>
    </div>

    <div class="task-question">
      <label>Comments</label>
      <textarea name="comments"></textarea>
    </div>

    <button class="btn-submit" name="submit" type="submit">Submit answers</button>
  </form>
</body></html>
"""

CLICKWORKER_TASK_SUCCESS_HTML = """
<html><body>
  <div class="task-success">
    <h3>Thank you!</h3>
    <p class="alert-success">Your answers have been submitted successfully.</p>
    <div class="earnings">+$5.00</div>
  </div>
</body></html>
"""

CLICKWORKER_APPLY_PAGE_HTML = """
<html><body>
  <article class="job-detail">
    <h1 class="job-title">Product Description Writing</h1>
    <p class="job-description">Write a short product description.</p>
    <button name="apply" class="btn-apply" data-swap="task-app-form">Apply now</button>
  </article>
</body></html>
"""

CLICKWORKER_EMPTY_PAGE_HTML = """
<html><body>
  <div class="task-info">No active work items right now.</div>
</body></html>
"""


def build_page(html: str, screens=None, url: str = "https://www.clickworker.com") -> FakePage:
    """Build a FakePage from a fixture; raises if lxml is unavailable."""
    if _HTML_LIB is None:  # pragma: no cover
        raise RuntimeError("lxml is required for FakePage tests")
    return FakePage(html, screens=screens, url=url)


def lxml_available() -> bool:
    return _HTML_LIB is not None
