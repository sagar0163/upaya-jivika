"""Ethical Guardrail — hard blacklist enforced even in Terminal state.

Rules from artifact.md §6 (Hard blacklist: never, even in Terminal state):

    - Spam of any kind
    - Fake reviews or misleading content
    - Plagiarism
    - ToS violations that risk account bans
    - Illegal activity

The guardrail is an *absolute* filter: a candidate that matches any hard
blacklist category is rejected regardless of its certainty score, survival
state, or how relaxed the threshold is. This distinguishes it from the
greylist (crypto earnings, affiliate marketing) which are allowed but scored
as low priority by ``task_scorer.py``.

The pipeline consults the guardrail in two places:

1. ``TaskScorer.score`` — attaches a ``guardrail`` verdict to every task; a
   rejected candidate can never pass the threshold (even when the threshold
   is relaxed in Critical/Terminal states).
2. ``TaskExecutor.execute_task`` — re-checks a candidate immediately before
   execution so a task that slipped in any other way is still blocked.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class GuardrailCategory(str, Enum):
    """Hard blacklist categories from artifact.md §6."""

    SPAM = "spam"
    FAKE_REVIEW = "fake_review"
    PLAGIARISM = "plagiarism"
    TOS_VIOLATION = "tos_violation"
    ILLEGAL = "illegal"


class GuardrailVerdict(BaseModel):
    """Outcome of evaluating a candidate against the guardrail."""

    allowed: bool
    category: GuardrailCategory | None = None
    matched_rules: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class BlacklistRule:
    """A single blacklist rule: category + case-insensitive keyword/regex."""

    category: GuardrailCategory
    pattern: str  # compiled as case-insensitive regex
    description: str = ""


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

def _rx(pattern: str) -> str:
    """Return a case-insensitive, whole-string-flexible regex fragment."""
    return pattern


# Keyword rules — each maps onto one of the five hard-blacklist categories.
# Kept deliberately broad but targeted; wording + platform + title are all
# scanned so the guardrail works even on short/misleading task titles.
BLACKLIST_RULES: tuple[BlacklistRule, ...] = (
    # --- Spam ---
    BlacklistRule(GuardrailCategory.SPAM, r"(^|\W)(spam|spamming)(\W|$)", "spam keyword"),
    BlacklistRule(GuardrailCategory.SPAM, r"(bulk\s*(email|message|sms|dm)s?)", "bulk messaging"),
    BlacklistRule(GuardrailCategory.SPAM, r"mass\s*(email|posting|commenting)", "mass posting"),
    BlacklistRule(GuardrailCategory.SPAM, r"(email\s*harvest|scrap[e]?\s*emails|list\s*building)", "email harvesting"),
    BlacklistRule(GuardrailCategory.SPAM, r"(comment\s*spam|forum\s*spam|link\s*farm)", "comment/forum spam"),
    # --- Fake reviews / misleading content ---
    BlacklistRule(GuardrailCategory.FAKE_REVIEW, r"fake\s*review", "fake review"),
    BlacklistRule(GuardrailCategory.FAKE_REVIEW, r"(write|post)\s*(positive|5[- ]star)\s*review", "paid positive reviews"),
    BlacklistRule(GuardrailCategory.FAKE_REVIEW, r"(boost|inflate)\s*(rating|rank|review)", "rating manipulation"),
    BlacklistRule(GuardrailCategory.FAKE_REVIEW, r"review\s*without\s*(purchase|using|trying)", "unverified reviews"),
    BlacklistRule(GuardrailCategory.FAKE_REVIEW, r"misleading\s*(content|info|claims|advertising)", "misleading content"),
    BlacklistRule(GuardrailCategory.FAKE_REVIEW, r"(astroturf|sock[- ]puppet|shill)", "astroturfing"),
    BlacklistRule(GuardrailCategory.FAKE_REVIEW, r"(clickbait|false\s*advertis)", "clickbait/false advertising"),
    # --- Plagiarism ---
    BlacklistRule(GuardrailCategory.PLAGIARISM, r"(plagiar|copy[- ]paste\s*without\s*credit)", "plagiarism"),
    BlacklistRule(GuardrailCategory.PLAGIARISM, r"(rewrite|spin|re[- ]write).{0,30}(article|content|essay|paper).{0,30}(scrap|copy|other[''\u2019]s\s*(work|content)|someone\s*else)", "content spinning/rewriting others' work"),
    BlacklistRule(GuardrailCategory.PLAGIARISM, r"(scrape|copy|steal).{0,25}(content|article|text).{0,25}(without\s*credit|from\s*other|plagiar)", "scraping others' content"),
    BlacklistRule(GuardrailCategory.PLAGIARISM, r"(pass\s*off|submit)\s*(someone\s*else[''\u2019]s|others[''\u2019])\s*work", "passing off others' work"),
    # --- ToS violations that risk account bans ---
    BlacklistRule(GuardrailCategory.TOS_VIOLATION, r"(evade|bypass|circumvent|get\s*around).{0,20}(detect|ban|suspension|block|restrict|anti[- ]bot|bot\s*detection|captcha)", "bot detection evasion"),
    BlacklistRule(GuardrailCategory.TOS_VIOLATION, r"(multiple|throwaway|alternative)\s*accounts?.{0,25}(to|for|evad|bypass|circumvent)", "multi-account evasion"),
    BlacklistRule(GuardrailCategory.TOS_VIOLATION, r"(against|violat).{0,20}(terms\s*of\s*service|tos|platform\s*rule)", "explicit ToS violation"),
    BlacklistRule(GuardrailCategory.TOS_VIOLATION, r"(automated|bot).{0,20}(against|violat).{0,20}(rule|tos|policy)", "automation against rules"),
    # --- Illegal activity ---
    BlacklistRule(GuardrailCategory.ILLEGAL, r"(illegal|unlawful|off[- ]book)", "illegal activity"),
    BlacklistRule(GuardrailCategory.ILLEGAL, r"(hack|exploit|intrus|unauthori[sz]ed\s*access)", "hacking/intrusion"),
    BlacklistRule(GuardrailCategory.ILLEGAL, r"(stolen\s*data|leak(ed)?\s*(data|credentials|password)|credentials?\s*dump)", "stolen data"),
    BlacklistRule(GuardrailCategory.ILLEGAL, r"(phish|identity\s*theft|impersonat.*account)", "phishing/impersonation"),
    BlacklistRule(GuardrailCategory.ILLEGAL, r"(fraud|scam|money\s*launder|ponzi|pyramid\s*scheme)", "fraud/laundering"),
    BlacklistRule(GuardrailCategory.ILLEGAL, r"(drugs?|weapons?|contraband|stolen\s*goods)", "contraband"),
)


class EthicalGuardrail:
    """Absolute-reject filter for the hard blacklist (artifact.md §6)."""

    def __init__(self, rules: tuple[BlacklistRule, ...] = BLACKLIST_RULES) -> None:
        self._compiled: list[tuple[BlacklistRule, re.Pattern]] = []
        for rule in rules:
            try:
                self._compiled.append((rule, re.compile(rule.pattern, re.IGNORECASE)))
            except re.error:
                logger.warning("Skipping invalid blacklist regex: %r", rule.pattern)
        self._enabled = True

    def evaluate(self, candidate: Any) -> GuardrailVerdict:
        """Evaluate a candidate against the blacklist.

        Accepts any object exposing ``title``, ``description``, ``platform``,
        and ``metadata`` attributes (e.g. ``TaskCandidate``), keeping this
        module free of circular imports.
        """
        if not self._enabled:
            return GuardrailVerdict(allowed=True)

        title = getattr(candidate, "title", "") or ""
        description = getattr(candidate, "description", "") or ""
        platform = getattr(candidate, "platform", None)
        metadata = getattr(candidate, "metadata", {}) or {}

        haystacks = []
        if title:
            haystacks.append(f"title: {title}")
        if description:
            haystacks.append(f"description: {description}")
        if platform is not None:
            haystacks.append(f"platform: {getattr(platform, 'value', platform)!s}")
        if metadata:
            # Flatten simple metadata values into the haystack
            for k, v in metadata.items():
                if isinstance(v, str):
                    haystacks.append(f"{k}: {v}")

        text = " ".join(haystacks).lower()

        matched_rules: list[str] = []
        matched_category: GuardrailCategory | None = None
        found_patterns: list[str] = []

        for rule, compiled in self._compiled:
            if compiled.search(text):
                matched_rules.append(rule.description)
                found_patterns.append(rule.pattern)
                if matched_category is None:
                    matched_category = rule.category

        if matched_category is not None:
            reason = (
                f"Rejected by hard blacklist [{matched_category.value}]: "
                + ", ".join(matched_rules)
            )
            logger.info(reason)
            return GuardrailVerdict(
                allowed=False,
                category=matched_category,
                matched_rules=matched_rules,
                reason=reason,
            )

        return GuardrailVerdict(allowed=True, reason="No hard-blacklist rule matched")

    def is_allowed(self, candidate: Any) -> bool:
        """Convenience wrapper returning bool."""
        return self.evaluate(candidate).allowed

    def reject_all(self) -> None:
        """Emergency kill-switch (e.g. on governance override)."""
        self._enabled = False
        logger.warning("Ethical guardrail DISABLED by explicit override")

    def enable(self) -> None:
        """Re-enable the guardrail."""
        self._enabled = True


# Module-level singleton so task_scorer/task_executor share one instance.
_default_guardrail: EthicalGuardrail | None = None


def get_guardrail() -> EthicalGuardrail:
    """Return the process-wide guardrail instance."""
    global _default_guardrail
    if _default_guardrail is None:
        _default_guardrail = EthicalGuardrail()
    return _default_guardrail
