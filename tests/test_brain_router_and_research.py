"""Unit tests for brain_router.py and research_loop.py.

All HTTP calls are mocked - no real API keys needed.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Set dummy env vars before importing modules
os.environ.setdefault("NVIDIA_API_KEY", "test-nvidia-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("MISTRAL_API_KEY", "test-mistral-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("CEREBRAS_API_KEY", "test-cerebras-key")
os.environ.setdefault("CLOUDFLARE_API_KEY", "test-cloudflare-key")
os.environ.setdefault("FREE_LLM_API_KEY", "test-freellm-key")


# ============================================================================
# Test brain_router.py
# ============================================================================

class TestProviderEnum:
    """Test Provider enum values."""

    def test_provider_values(self):
        from src.brain_router import Provider
        assert Provider.NVIDIA_NIM.value == "nvidia_nim"
        assert Provider.GROQ.value == "groq"
        assert Provider.GEMINI_FLASH.value == "gemini_flash"
        assert Provider.CEREBRAS.value == "cerebras"
        assert Provider.MISTRAL.value == "mistral"
        assert Provider.OPENROUTER.value == "openrouter"
        assert Provider.CLOUDFLARE.value == "cloudflare"
        assert Provider.FREE_LLM_API.value == "freellmapi"


class TestTaskTypeEnum:
    """Test TaskType enum values."""

    def test_task_type_values(self):
        from src.brain_router import TaskType
        assert TaskType.COMPLEX.value == "complex"
        assert TaskType.SPEED.value == "speed"
        assert TaskType.HIGH_VOLUME.value == "high_volume"
        assert TaskType.GENERAL.value == "general"


class TestCompletionRequest:
    """Test CompletionRequest model."""

    def test_default_values(self):
        from src.brain_router import CompletionRequest, TaskType
        req = CompletionRequest(prompt="test")
        assert req.prompt == "test"
        assert req.model is None
        assert req.max_tokens == 2048
        assert req.temperature == 0.7
        assert req.task_type == TaskType.GENERAL
        assert req.system_prompt is None

    def test_custom_values(self):
        from src.brain_router import CompletionRequest, TaskType
        req = CompletionRequest(
            prompt="test",
            model="custom-model",
            max_tokens=100,
            temperature=0.5,
            task_type=TaskType.COMPLEX,
            system_prompt="system"
        )
        assert req.model == "custom-model"
        assert req.max_tokens == 100
        assert req.temperature == 0.5
        assert req.task_type == TaskType.COMPLEX
        assert req.system_prompt == "system"


class TestCompletionResponse:
    """Test CompletionResponse model."""

    def test_success_response(self):
        from src.brain_router import CompletionResponse, Provider
        resp = CompletionResponse(
            content="Hello world",
            provider=Provider.NVIDIA_NIM,
            model="llama-3.1-70b",
            tokens_used=100,
            latency_ms=500,
            success=True
        )
        assert resp.content == "Hello world"
        assert resp.provider == Provider.NVIDIA_NIM
        assert resp.success is True
        assert resp.error is None

    def test_error_response(self):
        from src.brain_router import CompletionResponse, Provider
        resp = CompletionResponse(
            content="",
            provider=Provider.GROQ,
            model="llama-3.1-70b",
            success=False,
            error="Rate limit exceeded"
        )
        assert resp.success is False
        assert resp.error == "Rate limit exceeded"


class TestRoutingOrder:
    """Test provider routing order logic."""

    def test_complex_routing_order(self):
        from src.brain_router import Provider, TaskType, get_routing_order
        order = get_routing_order(TaskType.COMPLEX)
        assert order[0] == Provider.NVIDIA_NIM
        assert order[1] == Provider.CEREBRAS
        assert Provider.GROQ in order
        assert Provider.CLOUDFLARE == order[-1]  # Last resort

    def test_speed_routing_order(self):
        from src.brain_router import Provider, TaskType, get_routing_order
        order = get_routing_order(TaskType.SPEED)
        assert order[0] == Provider.GROQ
        assert order[1] == Provider.NVIDIA_NIM

    def test_high_volume_routing_order(self):
        from src.brain_router import Provider, TaskType, get_routing_order
        order = get_routing_order(TaskType.HIGH_VOLUME)
        assert order[0] == Provider.GEMINI_FLASH
        assert order[1] == Provider.FREE_LLM_API

    def test_general_routing_order(self):
        from src.brain_router import Provider, TaskType, get_routing_order
        order = get_routing_order(TaskType.GENERAL)
        assert order[0] == Provider.FREE_LLM_API


class TestBrainRouter:
    """Test BrainRouter class."""

    @pytest.fixture
    def router(self):
        # Reset singleton
        import src.brain_router as br
        from src.brain_router import BrainRouter
        br._router = None
        return BrainRouter()

    def test_initialization(self, router):
        """Test router initializes with available providers."""
        from src.brain_router import Provider
        # Should have at least NVIDIA client (dummy key set)
        assert Provider.NVIDIA_NIM in router.clients or not router._initialized

    @pytest.mark.asyncio
    async def test_complete_success_first_provider(self, router):
        """Test successful completion on first provider."""
        from src.brain_router import CompletionRequest, CompletionResponse, Provider, TaskType

        # Mock the NVIDIA client
        mock_client = AsyncMock()
        mock_client.complete.return_value = CompletionResponse(
            content="Test response",
            provider=Provider.NVIDIA_NIM,
            model="llama-3.1-70b",
            tokens_used=50,
            latency_ms=100,
            success=True
        )
        router.clients[Provider.NVIDIA_NIM] = mock_client
        router._initialized = True

        request = CompletionRequest(prompt="Test prompt", task_type=TaskType.COMPLEX)
        response = await router.complete(request)

        assert response.success is True
        assert response.content == "Test response"
        assert response.provider == Provider.NVIDIA_NIM
        mock_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_failover_to_second_provider(self, router):
        """Test failover when first provider fails."""
        from src.brain_router import CompletionRequest, CompletionResponse, Provider, TaskType

        # Mock NVIDIA client to fail
        mock_nvidia = AsyncMock()
        mock_nvidia.complete.return_value = CompletionResponse(
            content="",
            provider=Provider.NVIDIA_NIM,
            model="llama-3.1-70b",
            success=False,
            error="Rate limit exceeded"
        )

        # Mock Groq client to succeed
        mock_groq = AsyncMock()
        mock_groq.complete.return_value = CompletionResponse(
            content="Groq response",
            provider=Provider.GROQ,
            model="llama-3.1-70b",
            tokens_used=60,
            latency_ms=200,
            success=True
        )

        router.clients[Provider.NVIDIA_NIM] = mock_nvidia
        router.clients[Provider.GROQ] = mock_groq
        router._initialized = True

        request = CompletionRequest(prompt="Test prompt", task_type=TaskType.COMPLEX)
        response = await router.complete(request)

        assert response.success is True
        assert response.content == "Groq response"
        assert response.provider == Provider.GROQ
        mock_nvidia.complete.assert_called_once()
        mock_groq.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_all_providers_fail(self, router):
        """Test when all providers fail."""
        from src.brain_router import CompletionRequest, CompletionResponse, Provider, TaskType

        mock_client = AsyncMock()
        mock_client.complete.return_value = CompletionResponse(
            content="",
            provider=Provider.NVIDIA_NIM,
            model="llama-3.1-70b",
            success=False,
            error="API error"
        )
        router.clients[Provider.NVIDIA_NIM] = mock_client
        router._initialized = True

        request = CompletionRequest(prompt="Test prompt", task_type=TaskType.COMPLEX)
        response = await router.complete(request)

        assert response.success is False
        assert "All providers failed" in response.error


class TestConvenienceFunction:
    """Test the convenience complete() function."""

    @pytest.mark.asyncio
    async def test_complete_function(self):
        from src.brain_router import CompletionResponse, Provider, TaskType, complete

        with patch("src.brain_router.get_brain_router") as mock_get_router:
            mock_router = AsyncMock()
            mock_router.complete.return_value = CompletionResponse(
                content="Direct response",
                provider=Provider.NVIDIA_NIM,
                model="llama-3.1-70b",
                success=True
            )
            mock_get_router.return_value = mock_router

            response = await complete(
                prompt="Test",
                task_type=TaskType.COMPLEX,
                max_tokens=100,
                temperature=0.5
            )

            assert response.success is True
            assert response.content == "Direct response"
            mock_router.complete.assert_called_once()


# ============================================================================
# Test research_loop.py
# ============================================================================

class TestResearchEnums:
    """Test research loop enums."""

    def test_research_topic_values(self):
        from src.research_loop import ResearchTopic
        assert ResearchTopic.EARNING_PLATFORMS.value == "earning_platforms"
        assert ResearchTopic.PLATFORM_TOS.value == "platform_tos"
        assert ResearchTopic.PAY_RATES.value == "pay_rates"
        assert ResearchTopic.TASK_AVAILABILITY.value == "task_availability"
        assert ResearchTopic.USER_REVIEWS.value == "user_reviews"
        assert ResearchTopic.PAYMENT_METHODS.value == "payment_methods"

    def test_research_state_values(self):
        from src.research_loop import ResearchState
        assert ResearchState.PENDING.value == "pending"
        assert ResearchState.SEARCHING.value == "searching"
        assert ResearchState.READING.value == "reading"
        assert ResearchState.REASONING.value == "reasoning"
        assert ResearchState.COMPLETED.value == "completed"
        assert ResearchState.FAILED.value == "failed"


class TestSearchResult:
    """Test SearchResult model."""

    def test_search_result_creation(self):
        from src.research_loop import SearchResult
        result = SearchResult(
            title="Test Title",
            href="https://example.com",
            body="Test body content"
        )
        assert result.title == "Test Title"
        assert result.href == "https://example.com"
        assert result.body == "Test body content"


class TestResearchQuery:
    """Test ResearchQuery model."""

    def test_research_query_defaults(self):
        from src.research_loop import ResearchQuery, ResearchTopic
        query = ResearchQuery(topic=ResearchTopic.EARNING_PLATFORMS, query="test query")
        assert query.topic == ResearchTopic.EARNING_PLATFORMS
        assert query.query == "test query"
        assert query.max_results == 10
        assert query.priority == 1

    def test_research_query_custom(self):
        from src.research_loop import ResearchQuery, ResearchTopic
        query = ResearchQuery(
            topic=ResearchTopic.PAY_RATES,
            query="custom query",
            max_results=5,
            priority=2
        )
        assert query.max_results == 5
        assert query.priority == 2


class TestDDGSearcher:
    """Test DDGSearcher with mocked DDGS."""

    @pytest.fixture
    def searcher(self):
        from src.research_loop import DDGSearcher
        return DDGSearcher(max_rpm=30)

    @pytest.mark.asyncio
    async def test_search_success(self, searcher):
        """Test successful search with mocked DDGS."""
        mock_results = [
            {"title": "Result 1", "href": "https://example.com/1", "body": "Body 1"},
            {"title": "Result 2", "href": "https://example.com/2", "body": "Body 2"},
        ]

        with patch.object(searcher.ddgs, "text", return_value=mock_results):
            results = await searcher.search("test query", max_results=2)

        assert len(results) == 2
        assert results[0].title == "Result 1"
        assert results[0].href == "https://example.com/1"
        assert results[1].title == "Result 2"

    @pytest.mark.asyncio
    async def test_search_rate_limit(self, searcher):
        """Test rate limiting."""
        searcher._request_count = 30  # At limit
        searcher._last_reset = asyncio.get_event_loop().time()

        with pytest.raises(Exception, match="DDG rate limit exceeded"):
            await searcher.search("test query")


class TestJinaReader:
    """Test JinaReader with mocked HTTP."""

    @pytest.fixture
    def reader(self):
        from src.research_loop import JinaReader
        return JinaReader()

    @pytest.mark.asyncio
    async def test_read_success(self, reader):
        """Test successful URL reading."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "# Title\n\nContent here"

        with patch.object(reader.client, "get", return_value=mock_response) as mock_get:
            content = await reader.read("https://example.com/article")

        assert content == "# Title\n\nContent here"
        mock_get.assert_called_once()
        # Verify Jina URL format
        call_args = mock_get.call_args[0][0]
        assert "r.jina.ai" in call_args

    @pytest.mark.asyncio
    async def test_read_http_error(self, reader):
        """Test handling of HTTP errors."""
        mock_response = Mock()
        mock_response.status_code = 404

        with patch.object(reader.client, "get", return_value=mock_response):
            content = await reader.read("https://example.com/notfound")

        assert content is None

    @pytest.mark.asyncio
    async def test_read_exception(self, reader):
        """Test handling of exceptions."""
        with patch.object(reader.client, "get", side_effect=Exception("Network error")):
            content = await reader.read("https://example.com/error")

        assert content is None

    @pytest.mark.asyncio
    async def test_read_rate_limit(self, reader):
        """Test rate limiting."""
        reader._request_count = 200  # At limit
        reader._last_reset = asyncio.get_event_loop().time()

        content = await reader.read("https://example.com/test")
        assert content is None


class TestResearchAgent:
    """Test ResearchAgent with mocked dependencies."""

    @pytest.fixture
    def agent(self):
        from src.research_loop import ResearchAgent
        return ResearchAgent()

    @pytest.mark.asyncio
    async def test_research_success(self, agent):
        """Test full research cycle with mocked search, read, and reasoning."""
        from src.research_loop import ResearchQuery, ResearchState, ResearchTopic

        # Mock searcher
        mock_search_results = [
            Mock(title="Article 1", href="https://ex.com/1", body="Snippet 1"),
            Mock(title="Article 2", href="https://ex.com/2", body="Snippet 2"),
        ]
        agent.searcher.search = AsyncMock(return_value=mock_search_results)

        # Mock reader
        agent.reader.read = AsyncMock(return_value="Full article content here...")

        # Mock router for reasoning
        mock_reasoning_response = Mock()
        mock_reasoning_response.success = True
        mock_reasoning_response.content = json.dumps({
            "summary": "Clickworker pays $5-10/hour via Payoneer in India.",
            "confidence": 0.85,
            "key_facts": ["Payoneer supported", "Hourly rate $5-10"],
            "warnings": ["Account verification required"]
        })
        agent.router.complete = AsyncMock(return_value=mock_reasoning_response)

        query = ResearchQuery(
            topic=ResearchTopic.EARNING_PLATFORMS,
            query="Clickworker pay rates India",
            max_results=2
        )

        result = await agent.research(query)

        assert result.topic == ResearchTopic.EARNING_PLATFORMS
        assert result.query == "Clickworker pay rates India"
        assert result.state == ResearchState.COMPLETED
        assert result.confidence == 0.85
        assert "Clickworker pays" in result.summary
        assert len(result.findings) == 2
        assert len(result.sources) == 2

    @pytest.mark.asyncio
    async def test_research_no_findings(self, agent):
        """Test research with no search results."""
        from src.research_loop import ResearchQuery, ResearchTopic

        agent.searcher.search = AsyncMock(return_value=[])

        query = ResearchQuery(topic=ResearchTopic.EARNING_PLATFORMS, query="nonexistent")
        result = await agent.research(query)

        assert result.confidence == 0.0
        assert "No relevant findings" in result.summary
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_research_reasoning_failure(self, agent):
        """Test handling of reasoning failure."""
        from src.research_loop import ResearchQuery, ResearchTopic

        mock_search_results = [
            Mock(title="Article 1", href="https://ex.com/1", body="Snippet 1"),
        ]
        agent.searcher.search = AsyncMock(return_value=mock_search_results)
        agent.reader.read = AsyncMock(return_value="Content")

        # Router fails
        mock_reasoning_response = Mock()
        mock_reasoning_response.success = False
        mock_reasoning_response.error = "NVIDIA rate limited"
        agent.router.complete = AsyncMock(return_value=mock_reasoning_response)

        query = ResearchQuery(topic=ResearchTopic.EARNING_PLATFORMS, query="test")
        result = await agent.research(query)

        assert result.confidence == 0.0
        assert "Reasoning failed" in result.summary


class TestResearchLoop:
    """Test ResearchLoop scheduler."""

    @pytest.fixture
    def loop(self):
        from src.research_loop import ResearchAgent, ResearchLoop
        agent = ResearchAgent()
        return ResearchLoop(agent=agent)

    @pytest.mark.asyncio
    async def test_start_stop(self, loop):
        """Test starting and stopping the loop."""
        assert loop._running is False

        await loop.start()
        assert loop._running is True
        assert loop._task is not None

        await loop.stop()
        assert loop._running is False

    @pytest.mark.asyncio
    async def test_trigger(self, loop):
        """Test manual trigger."""
        await loop.start()
        loop.trigger()
        assert loop._trigger_event.is_set()
        await loop.stop()

    def test_get_status(self, loop):
        """Test status reporting."""
        status = loop.get_status()
        assert status["running"] is False
        assert status["interval_hours"] == 6
        assert status["last_run"] is None
        assert status["history_count"] == 0


class TestQuickResearch:
    """Test quick_research convenience function."""

    @pytest.mark.asyncio
    async def test_quick_research(self):
        from src.research_loop import ResearchResult, ResearchTopic, quick_research

        with patch("src.research_loop.ResearchAgent") as MockAgent:
            mock_agent = AsyncMock()
            mock_agent.research.return_value = ResearchResult(
                topic=ResearchTopic.EARNING_PLATFORMS,
                query="test",
                findings=[],
                summary="Test summary",
                confidence=0.9,
                sources=["https://example.com"]
            )
            MockAgent.return_value = mock_agent

            result = await quick_research("test query", ResearchTopic.PAY_RATES)

            assert isinstance(result, ResearchResult)
            assert result.summary == "Test summary"
            assert result.confidence == 0.9
            mock_agent.close.assert_called_once()


class TestSurvivalResearchQueries:
    """Test pre-defined survival research queries."""

    def test_survival_queries_exist(self):
        from src.research_loop import SURVIVAL_RESEARCH_QUERIES, ResearchTopic
        assert len(SURVIVAL_RESEARCH_QUERIES) >= 6

        topics = [q.topic for q in SURVIVAL_RESEARCH_QUERIES]
        assert ResearchTopic.EARNING_PLATFORMS in topics
        assert ResearchTopic.PAYMENT_METHODS in topics
        assert ResearchTopic.PLATFORM_TOS in topics

        for query in SURVIVAL_RESEARCH_QUERIES:
            assert query.max_results >= 10
            assert len(query.query) > 10


# ============================================================================
# Integration-style tests
# ============================================================================

class TestBrainRouterIntegration:
    """Integration tests for brain_router with multiple providers."""

    @pytest.mark.asyncio
    async def test_full_failover_chain(self):
        """Test complete failover chain: NVIDIA -> Cerebras -> Mistral -> OpenRouter."""
        from src.brain_router import BrainRouter, CompletionRequest, CompletionResponse, Provider, TaskType

        router = BrainRouter()
        router._initialized = True

        # For COMPLEX task type, routing order is:
        # NVIDIA_NIM -> CEREBRAS -> MISTRAL -> OPENROUTER -> GROQ -> GEMINI_FLASH -> FREE_LLM_API -> CLOUDFLARE
        # We'll mock the first 4 in the chain
        providers_in_chain = [
            Provider.NVIDIA_NIM,
            Provider.CEREBRAS,
            Provider.MISTRAL,
            Provider.OPENROUTER,
        ]

        mock_clients = {}
        for i, provider in enumerate(providers_in_chain):
            mock_client = AsyncMock()
            if i < len(providers_in_chain) - 1:
                # All but last fail
                mock_client.complete.return_value = CompletionResponse(
                    content="",
                    provider=provider,
                    model="test",
                    success=False,
                    error=f"{provider.value} failed"
                )
            else:
                # Last one succeeds
                mock_client.complete.return_value = CompletionResponse(
                    content=f"Success from {provider.value}",
                    provider=provider,
                    model="test",
                    tokens_used=100,
                    latency_ms=100,
                    success=True
                )
            router.clients[provider] = mock_client
            mock_clients[provider] = mock_client

        request = CompletionRequest(prompt="Test", task_type=TaskType.COMPLEX)
        response = await router.complete(request)

        assert response.success is True
        assert response.provider == Provider.OPENROUTER
        assert "Success from openrouter" in response.content

        # Verify the chain was followed: NVIDIA, Cerebras, Mistral, OpenRouter all called
        for provider in providers_in_chain:
            mock_clients[provider].complete.assert_called_once()


# ============================================================================
# Pytest configuration
# ============================================================================

def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])