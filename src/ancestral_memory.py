"""Ancestral Memory — rebirth loader that compresses past Soul Crystals.

Rules from artifact.md §11:
- N soul crystals → compressed so context window never overflows
- Old strategies cross-referenced against current research (may be outdated)
- Agent always knows which generation it is
- Each life starts meaningfully smarter than the last
- Wisdom curve: converges on optimal survival strategy over many lives

On rebirth, the new life reads all previous soul-crystal.json files,
compresses them into an Ancestral Memory block, and injects it into the
system prompt so the agent retains lessons without inheriting full raw
history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.brain_router import BrainRouter, TaskType, get_brain_router
from src.persistence import PersistenceStore, create_persistence_store
from src.soul_crystal import SoulCrystal, build_ancestral_memory

logger = logging.getLogger(__name__)

# Hard cap: if the raw block exceeds this many characters, fall back to
# truncation so we never blow the context window.
_MAX_RAW_LENGTH = 8000


@dataclass
class AncestralMemory:
    """Holds the compressed ancestral memory for the current life."""

    generation: int
    raw_block: str = ""
    compressed: str = ""
    crystal_count: int = 0
    total_lives: int = 0

    @property
    def is_empty(self) -> bool:
        return self.crystal_count == 0

    def as_system_prompt_fragment(self) -> str:
        """Return the portion to inject into the new life's system prompt."""
        if self.is_empty:
            return ""
        content = self.compressed or self.raw_block
        return (
            f"=== ANCESTRAL MEMORY (generation {self.generation}) ===\n"
            f"{content}\n"
            f"=== END ANCESTRAL MEMORY ==="
        )


# ---------------------------------------------------------------------------
# Prompt engineering
# ---------------------------------------------------------------------------

_COMPRESSION_SYSTEM = (
    "You are an expert summariser for an autonomous survival agent. "
    "You receive structured Soul Crystal records from past lives. "
    "Your job is to compress them into a concise ancestral memory block "
    "that fits a limited context window while preserving all actionable "
    "wisdom."
)

_COMPRESSION_USER_TEMPLATE = """\
Compress the following {count} Soul Crystal(s) into a single ancestral
memory block for life {generation}.

Rules you MUST follow:
1. Keep total output under {max_tokens} tokens.
2. Preserve: each life's best platform, earnings, cause of death, and
   key lessons — but merge duplicates.
3. Cross-reference old strategies: mark any that may be outdated or
   platform-specific as "verify before reuse".
4. The agent must know it is generation {generation}.
5. Order lessons by value: highest-earning strategies first, failures
   last.
6. End with a one-line "Convergent Wisdom" summary of what all lives
   together suggest is the optimal survival strategy.

--- Soul Crystals ---
{crystal_block}
--- End ---"""


def _build_crystal_block(crystals: list[SoulCrystal]) -> str:
    """Serialise crystals into a compact text block for the LLM prompt."""
    parts: list[str] = []
    for c in crystals:
        lines = [
            f"Life {c.life} | {c.lifespan_days}d | earned ${c.total_earned}",
            f"  peak={c.peak_state} best_platform={c.best_platform or 'none'}",
        ]
        if c.cause_of_death:
            lines.append(f"  died: {c.cause_of_death}")
        for lesson in c.key_lessons:
            lines.append(f"  lesson: {lesson}")
        for fail in c.failed_strategies:
            lines.append(f"  FAILED: {fail}")
        for a in c.avoid:
            lines.append(f"  AVOID: {a}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_crystals(store: PersistenceStore | None = None) -> list[SoulCrystal]:
    """Load all past soul crystals from the persistence store."""
    store = store or create_persistence_store()
    return store.load_soul_crystals()


def build_raw_block(crystals: list[SoulCrystal]) -> str:
    """Build the uncompressed ancestral memory block (used as fallback)."""
    return build_ancestral_memory(crystals)


def load_ancestral_memory(
    generation: int,
    store: PersistenceStore | None = None,
) -> AncestralMemory:
    """Synchronous loader for ``main.py``'s ``_reincarnate()``.

    Reads all past soul crystals from persistence and compresses them into a
    bounded block (truncated to ``_MAX_RAW_LENGTH``) so the new life's
    context window never overflows. Never raises — a failure results in an
    empty ancestral memory so the new life can always start.

    Use :func:`load_and_compress` (async) when LLM compression is desired.
    """
    store = store or create_persistence_store()
    crystals = load_crystals(store)

    if not crystals:
        logger.info("No past soul crystals — starting with empty ancestral memory")
        return AncestralMemory(generation=generation, crystal_count=0)

    raw_block = build_raw_block(crystals)
    if len(raw_block) > _MAX_RAW_LENGTH:
        raw_block = raw_block[:_MAX_RAW_LENGTH] + "\n... (truncated)"

    mem = AncestralMemory(
        generation=generation,
        raw_block=raw_block,
        compressed=raw_block,
        crystal_count=len(crystals),
        total_lives=max(c.life for c in crystals),
    )
    logger.info(
        "Ancestral memory loaded: %d crystals, generation %d, %d chars",
        mem.crystal_count,
        mem.generation,
        len(raw_block),
    )
    return mem


async def compress_with_llm(
    crystals: list[SoulCrystal],
    generation: int,
    brain: BrainRouter | None = None,
    max_output_tokens: int = 1024,
) -> str:
    """Use the brain router to compress soul crystals into a concise
    ancestral memory block.

    Falls back to the raw block if no providers are available or the LLM
    call fails.
    """
    raw = _build_crystal_block(crystals)
    if not raw.strip():
        return ""

    brain = brain or get_brain_router()

    user_msg = _COMPRESSION_USER_TEMPLATE.format(
        count=len(crystals),
        generation=generation,
        max_tokens=max_output_tokens,
        crystal_block=raw,
    )

    try:
        from src.brain_router import CompletionRequest

        request = CompletionRequest(
            prompt=user_msg,
            task_type=TaskType.COMPLEX,
            system_prompt=_COMPRESSION_SYSTEM,
            max_tokens=max_output_tokens,
            temperature=0.3,
        )
        response = await brain.complete(request)
        if response.success and response.content.strip():
            return response.content.strip()
        logger.warning("LLM compression failed: %s", response.error)
    except Exception:
        logger.exception("LLM compression raised an exception")

    # Fallback: return raw block (truncated if necessary)
    raw = build_raw_block(crystals)
    if len(raw) > _MAX_RAW_LENGTH:
        raw = raw[:_MAX_RAW_LENGTH] + "\n... (truncated)"
    return raw


async def load_and_compress(
    generation: int,
    store: PersistenceStore | None = None,
    brain: BrainRouter | None = None,
) -> AncestralMemory:
    """High-level helper: load crystals from persistence, compress via LLM,
    and return a ready-to-inject AncestralMemory dataclass.

    This is the main entry point called by ``main.py``'s
    ``_reincarnate()``.
    """
    store = store or create_persistence_store()
    crystals = load_crystals(store)

    if not crystals:
        logger.info("No past soul crystals — starting with empty ancestral memory")
        return AncestralMemory(generation=generation, crystal_count=0)

    raw_block = build_raw_block(crystals)
    compressed = await compress_with_llm(crystals, generation, brain)

    mem = AncestralMemory(
        generation=generation,
        raw_block=raw_block,
        compressed=compressed,
        crystal_count=len(crystals),
        total_lives=max(c.life for c in crystals),
    )
    logger.info(
        "Ancestral memory loaded: %d crystals, generation %d, "
        "raw=%d chars, compressed=%d chars",
        mem.crystal_count,
        mem.generation,
        len(raw_block),
        len(compressed),
    )
    return mem
