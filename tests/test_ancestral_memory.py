"""Unit tests for ancestral_memory.py — rebirth loader for Soul Crystals."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.ancestral_memory import (
    AncestralMemory,
    _build_crystal_block,
    compress_with_llm,
    load_ancestral_memory,
    load_and_compress,
    load_crystals,
)
from src.persistence import InMemoryStore
from src.soul_crystal import SoulCrystal


def _crystal(life: int, lessons=None, failed=None, avoid=None, platform=""):
    return SoulCrystal(
        life=life,
        born=datetime(2026, 9, 1, tzinfo=timezone.utc),
        died=datetime(2026, 9, 21, tzinfo=timezone.utc),
        lifespan_days=20,
        total_earned=Decimal("3.20"),
        peak_state="surviving",
        best_platform=platform,
        key_lessons=lessons or [f"Lesson from life {life}"],
        failed_strategies=failed or [],
        avoid=avoid or [],
        cause_of_death="Debt exceeded $10.00",
    )


def _store(crystals):
    store = InMemoryStore()
    for c in crystals:
        store.save_soul_crystal(c)
    return store


def _response(content, success=True, error=None):
    from src.brain_router import CompletionResponse, Provider
    return CompletionResponse(
        content=content,
        provider=Provider.NVIDIA_NIM,
        model="test",
        success=success,
        error=error,
    )


# ---------------------------------------------------------------------------
# AncestralMemory dataclass
# ---------------------------------------------------------------------------

class TestAncestralMemoryDataclass:
    def test_initial_generation(self):
        mem = AncestralMemory(generation=1)
        assert mem.generation == 1
        assert mem.crystal_count == 0
        assert mem.is_empty is True
        assert mem.as_system_prompt_fragment() == ""

    def test_uses_compressed_when_present(self):
        mem = AncestralMemory(
            generation=2,
            raw_block="raw block content",
            compressed="compressed content",
            crystal_count=1,
        )
        frag = mem.as_system_prompt_fragment()
        assert "generation 2" in frag
        assert "compressed content" in frag
        assert "raw block content" not in frag

    def test_falls_back_to_raw(self):
        mem = AncestralMemory(
            generation=3,
            raw_block="raw block content",
            compressed="",
            crystal_count=2,
        )
        frag = mem.as_system_prompt_fragment()
        assert "raw block content" in frag


# ---------------------------------------------------------------------------
# Crystal block building
# ---------------------------------------------------------------------------

class TestBuildCrystalBlock:
    def test_empty(self):
        assert load_crystals(_store([])) == []

    def test_includes_all_lives_and_rules(self):
        store = _store(
            [
                _crystal(1, lessons=["Data annotation pays faster"],
                         failed=["Fiverr writing - rejected gigs"],
                         avoid=["tasks taking >2 days"], platform="Clickworker"),
            ]
        )
        block = _build_crystal_block(load_crystals(store))
        assert "Life 1" in block
        assert "Clickworker" in block
        assert "Data annotation pays faster" in block
        assert "Fiverr writing - rejected gigs" in block
        assert "tasks taking >2 days" in block


# ---------------------------------------------------------------------------
# Synchronous loader (used by main.py _reincarnate)
# ---------------------------------------------------------------------------

class TestLoadAncestralMemory:
    def test_no_crystals_returns_empty(self):
        mem = load_ancestral_memory(2, _store([]))
        assert mem.generation == 2
        assert mem.crystal_count == 0
        assert mem.is_empty is True

    def test_loads_and_compress_crystals_from_store(self):
        store = _store([_crystal(1, platform="Clickworker"), _crystal(2)])
        mem = load_ancestral_memory(3, store)
        assert mem.crystal_count == 2
        assert mem.total_lives == 2
        assert mem.generation == 3
        assert mem.is_empty is False
        assert "Life 1" in mem.raw_block
        assert "Life 2" in mem.raw_block
        assert "Clickworker" in mem.raw_block
        assert mem.compressed == mem.raw_block


# ---------------------------------------------------------------------------
# LLM compression (async)
# ---------------------------------------------------------------------------

class TestCompressWithLLM:
    @pytest.mark.asyncio
    async def test_llm_success_returns_compressed(self):
        class FakeBrain:
            async def complete(self, request):
                return _response("LLM compressed wisdom")

        result = await compress_with_llm([_crystal(1)], 2, FakeBrain())
        assert result == "LLM compressed wisdom"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_raw(self):
        class FakeBrain:
            async def complete(self, request):
                return _response("", success=False, error="boom")

        result = await compress_with_llm([_crystal(1)], 2, FakeBrain())
        assert "Life 1" in result

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_raw(self):
        class FakeBrain:
            async def complete(self, request):
                raise RuntimeError("network down")

        result = await compress_with_llm([_crystal(1)], 2, FakeBrain())
        assert "Life 1" in result


class TestLoadAndCompress:
    @pytest.mark.asyncio
    async def test_no_crystals_returns_empty(self):
        class OfflineBrain:
            async def complete(self, request):
                return _response("")

        mem = await load_and_compress(2, store=_store([]), brain=OfflineBrain())
        assert mem.crystal_count == 0
        assert mem.is_empty is True
        assert mem.generation == 2

    @pytest.mark.asyncio
    async def test_loads_all_crystals_from_store(self):
        class OfflineBrain:
            async def complete(self, request):
                return _response("compressed summary")

        mem = await load_and_compress(3, store=_store([_crystal(1), _crystal(2)]),
                                      brain=OfflineBrain())
        assert mem.crystal_count == 2
        assert mem.total_lives == 2
        assert mem.generation == 3
        assert "Life 1" in mem.raw_block
        assert "Life 2" in mem.raw_block
        assert mem.compressed == "compressed summary"
