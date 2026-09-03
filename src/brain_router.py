"""Brain Router - Multi-provider AI routing with automatic failover.

Routing logic:
- Complex task  → NVIDIA NIM (no budget, just rate limit)
- Speed needed  → Groq
- High volume   → Gemini Flash
- NVIDIA limited → Cerebras → Mistral → OpenRouter
"""

from __future__ import annotations

import os
import asyncio
import logging
from enum import Enum
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Provider(str, Enum):
    """Available AI providers in priority order."""
    NVIDIA_NIM = "nvidia_nim"
    GROQ = "groq"
    GEMINI_FLASH = "gemini_flash"
    CEREBRAS = "cerebras"
    MISTRAL = "mistral"
    OPENROUTER = "openrouter"
    CLOUDFLARE = "cloudflare"
    FREE_LLM_API = "freellmapi"


class TaskType(str, Enum):
    """Task types that influence provider selection."""
    COMPLEX = "complex"          # NVIDIA NIM
    SPEED = "speed"              # Groq
    HIGH_VOLUME = "high_volume"  # Gemini Flash
    GENERAL = "general"          # Default routing


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""
    name: Provider
    base_url: str
    api_key_env: str
    models: List[str]
    rpm_limit: int
    timeout: float = 30.0
    enabled: bool = True


class CompletionRequest(BaseModel):
    """Request model for completions."""
    prompt: str
    model: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.7
    task_type: TaskType = TaskType.GENERAL
    system_prompt: Optional[str] = None


class CompletionResponse(BaseModel):
    """Response model for completions."""
    content: str
    provider: Provider
    model: str
    tokens_used: int = 0
    latency_ms: int = 0
    success: bool = True
    error: Optional[str] = None


class ProviderClient(ABC):
    """Abstract base class for provider clients."""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.api_key = os.getenv(config.api_key_env, "")
        self.client = httpx.AsyncClient(timeout=config.timeout)
        self._request_count = 0
        self._last_reset = 0.0

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Execute a completion request."""
        pass

    async def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits."""
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = asyncio.get_event_loop().time()
        if now - self._last_reset >= 60:
            self._request_count = 0
            self._last_reset = now
        return self._request_count < self.config.rpm_limit

    async def _increment_count(self):
        """Increment request counter."""
        self._request_count += 1

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


class NVIDIAClient(ProviderClient):
    """NVIDIA NIM client - primary for complex tasks."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not await self._check_rate_limit():
            return CompletionResponse(
                content="",
                provider=Provider.NVIDIA_NIM,
                model=request.model or self.config.models[0],
                success=False,
                error="Rate limit exceeded"
            )

        model = request.model or self.config.models[0]
        url = f"{self.config.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False
        }

        try:
            import time
            start = time.perf_counter()
            response = await self.client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                await self._increment_count()
                return CompletionResponse(
                    content=content,
                    provider=Provider.NVIDIA_NIM,
                    model=model,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )
            else:
                return CompletionResponse(
                    content="",
                    provider=Provider.NVIDIA_NIM,
                    model=model,
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}"
                )
        except Exception as e:
            return CompletionResponse(
                content="",
                provider=Provider.NVIDIA_NIM,
                model=model,
                success=False,
                error=str(e)
            )


class GroqClient(ProviderClient):
    """Groq client - speed layer."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not await self._check_rate_limit():
            return CompletionResponse(
                content="",
                provider=Provider.GROQ,
                model=request.model or self.config.models[0],
                success=False,
                error="Rate limit exceeded"
            )

        model = request.model or self.config.models[0]
        url = f"{self.config.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False
        }

        try:
            import time
            start = time.perf_counter()
            response = await self.client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                await self._increment_count()
                return CompletionResponse(
                    content=content,
                    provider=Provider.GROQ,
                    model=model,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )
            else:
                return CompletionResponse(
                    content="",
                    provider=Provider.GROQ,
                    model=model,
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}"
                )
        except Exception as e:
            return CompletionResponse(
                content="",
                provider=Provider.GROQ,
                model=model,
                success=False,
                error=str(e)
            )


class GeminiClient(ProviderClient):
    """Gemini Flash client - volume layer."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not await self._check_rate_limit():
            return CompletionResponse(
                content="",
                provider=Provider.GEMINI_FLASH,
                model=request.model or self.config.models[0],
                success=False,
                error="Rate limit exceeded"
            )

        model = request.model or self.config.models[0]
        url = f"{self.config.base_url}/models/{model}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key
        }

        parts = []
        if request.system_prompt:
            parts.append({"text": f"System: {request.system_prompt}\n\n"})
        parts.append({"text": request.prompt})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature
            }
        }

        try:
            import time
            start = time.perf_counter()
            response = await self.client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                content = data["candidates"][0]["content"]["parts"][0]["text"]
                tokens_used = data.get("usageMetadata", {}).get("totalTokenCount", 0)
                await self._increment_count()
                return CompletionResponse(
                    content=content,
                    provider=Provider.GEMINI_FLASH,
                    model=model,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )
            else:
                return CompletionResponse(
                    content="",
                    provider=Provider.GEMINI_FLASH,
                    model=model,
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}"
                )
        except Exception as e:
            return CompletionResponse(
                content="",
                provider=Provider.GEMINI_FLASH,
                model=model,
                success=False,
                error=str(e)
            )


class OpenRouterClient(ProviderClient):
    """OpenRouter client - model variety fallback."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not await self._check_rate_limit():
            return CompletionResponse(
                content="",
                provider=Provider.OPENROUTER,
                model=request.model or self.config.models[0],
                success=False,
                error="Rate limit exceeded"
            )

        model = request.model or self.config.models[0]
        url = f"{self.config.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/sagar0163/upaya-jivika",
            "X-Title": "Survival AI"
        }

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False
        }

        try:
            import time
            start = time.perf_counter()
            response = await self.client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                await self._increment_count()
                return CompletionResponse(
                    content=content,
                    provider=Provider.OPENROUTER,
                    model=model,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )
            else:
                return CompletionResponse(
                    content="",
                    provider=Provider.OPENROUTER,
                    model=model,
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}"
                )
        except Exception as e:
            return CompletionResponse(
                content="",
                provider=Provider.OPENROUTER,
                model=model,
                success=False,
                error=str(e)
            )


class MistralClient(ProviderClient):
    """Mistral client - large budget fallback."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not await self._check_rate_limit():
            return CompletionResponse(
                content="",
                provider=Provider.MISTRAL,
                model=request.model or self.config.models[0],
                success=False,
                error="Rate limit exceeded"
            )

        model = request.model or self.config.models[0]
        url = f"{self.config.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False
        }

        try:
            import time
            start = time.perf_counter()
            response = await self.client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                await self._increment_count()
                return CompletionResponse(
                    content=content,
                    provider=Provider.MISTRAL,
                    model=model,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )
            else:
                return CompletionResponse(
                    content="",
                    provider=Provider.MISTRAL,
                    model=model,
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}"
                )
        except Exception as e:
            return CompletionResponse(
                content="",
                provider=Provider.MISTRAL,
                model=model,
                success=False,
                error=str(e)
            )


class CerebrasClient(ProviderClient):
    """Cerebras client - fallback after NVIDIA."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not await self._check_rate_limit():
            return CompletionResponse(
                content="",
                provider=Provider.CEREBRAS,
                model=request.model or self.config.models[0],
                success=False,
                error="Rate limit exceeded"
            )

        model = request.model or self.config.models[0]
        url = f"{self.config.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False
        }

        try:
            import time
            start = time.perf_counter()
            response = await self.client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                await self._increment_count()
                return CompletionResponse(
                    content=content,
                    provider=Provider.CEREBRAS,
                    model=model,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )
            else:
                return CompletionResponse(
                    content="",
                    provider=Provider.CEREBRAS,
                    model=model,
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}"
                )
        except Exception as e:
            return CompletionResponse(
                content="",
                provider=Provider.CEREBRAS,
                model=model,
                success=False,
                error=str(e)
            )


class CloudflareClient(ProviderClient):
    """Cloudflare Workers AI client - last resort."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not await self._check_rate_limit():
            return CompletionResponse(
                content="",
                provider=Provider.CLOUDFLARE,
                model=request.model or self.config.models[0],
                success=False,
                error="Rate limit exceeded"
            )

        model = request.model or self.config.models[0]
        url = f"{self.config.base_url}/{model}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature
        }

        try:
            import time
            start = time.perf_counter()
            response = await self.client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                content = data["result"]["response"]
                tokens_used = 0  # Cloudflare doesn't return token counts
                await self._increment_count()
                return CompletionResponse(
                    content=content,
                    provider=Provider.CLOUDFLARE,
                    model=model,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )
            else:
                return CompletionResponse(
                    content="",
                    provider=Provider.CLOUDFLARE,
                    model=model,
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}"
                )
        except Exception as e:
            return CompletionResponse(
                content="",
                provider=Provider.CLOUDFLARE,
                model=model,
                success=False,
                error=str(e)
            )


class FreeLLMAPIClient(ProviderClient):
    """FreeLLMAPI client - router layer aggregator."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not await self._check_rate_limit():
            return CompletionResponse(
                content="",
                provider=Provider.FREE_LLM_API,
                model=request.model or self.config.models[0],
                success=False,
                error="Rate limit exceeded"
            )

        model = request.model or self.config.models[0]
        url = f"{self.config.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False
        }

        try:
            import time
            start = time.perf_counter()
            response = await self.client.post(url, headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                await self._increment_count()
                return CompletionResponse(
                    content=content,
                    provider=Provider.FREE_LLM_API,
                    model=model,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )
            else:
                return CompletionResponse(
                    content="",
                    provider=Provider.FREE_LLM_API,
                    model=model,
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}"
                )
        except Exception as e:
            return CompletionResponse(
                content="",
                provider=Provider.FREE_LLM_API,
                model=model,
                success=False,
                error=str(e)
            )


# Provider configuration
PROVIDER_CONFIGS = {
    Provider.NVIDIA_NIM: ProviderConfig(
        name=Provider.NVIDIA_NIM,
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        models=["meta/llama-3.1-70b-instruct", "meta/llama-3.1-8b-instruct"],
        rpm_limit=40,
    ),
    Provider.GROQ: ProviderConfig(
        name=Provider.GROQ,
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        models=["llama-3.1-70b-versatile", "llama-3.1-8b-instant"],
        rpm_limit=30,
    ),
    Provider.GEMINI_FLASH: ProviderConfig(
        name=Provider.GEMINI_FLASH,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_env="GEMINI_API_KEY",
        models=["gemini-1.5-flash", "gemini-1.5-flash-8b"],
        rpm_limit=1500,  # daily limit converted roughly
    ),
    Provider.CEREBRAS: ProviderConfig(
        name=Provider.CEREBRAS,
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        models=["llama3.1-70b", "llama3.1-8b"],
        rpm_limit=10,
    ),
    Provider.MISTRAL: ProviderConfig(
        name=Provider.MISTRAL,
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        models=["mistral-large-latest", "mistral-small-latest"],
        rpm_limit=2,
    ),
    Provider.OPENROUTER: ProviderConfig(
        name=Provider.OPENROUTER,
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        models=["meta-llama/llama-3.1-70b-instruct:free", "google/gemini-flash-1.5:free"],
        rpm_limit=50,
    ),
    Provider.CLOUDFLARE: ProviderConfig(
        name=Provider.CLOUDFLARE,
        base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run",
        api_key_env="CLOUDFLARE_API_KEY",
        models=["@cf/meta/llama-3.1-8b-instruct"],
        rpm_limit=10000,  # neurons/day converted roughly
    ),
    Provider.FREE_LLM_API: ProviderConfig(
        name=Provider.FREE_LLM_API,
        base_url="https://api.freellmapi.com/v1",
        api_key_env="FREE_LLM_API_KEY",
        models=["auto"],
        rpm_limit=100,
    ),
}

# Provider client mapping
CLIENT_CLASSES = {
    Provider.NVIDIA_NIM: NVIDIAClient,
    Provider.GROQ: GroqClient,
    Provider.GEMINI_FLASH: GeminiClient,
    Provider.CEREBRAS: CerebrasClient,
    Provider.MISTRAL: MistralClient,
    Provider.OPENROUTER: OpenRouterClient,
    Provider.CLOUDFLARE: CloudflareClient,
    Provider.FREE_LLM_API: FreeLLMAPIClient,
}


def get_routing_order(task_type: TaskType) -> List[Provider]:
    """Get provider routing order based on task type."""
    if task_type == TaskType.COMPLEX:
        return [
            Provider.NVIDIA_NIM,
            Provider.CEREBRAS,
            Provider.MISTRAL,
            Provider.OPENROUTER,
            Provider.GROQ,
            Provider.GEMINI_FLASH,
            Provider.FREE_LLM_API,
            Provider.CLOUDFLARE,
        ]
    elif task_type == TaskType.SPEED:
        return [
            Provider.GROQ,
            Provider.NVIDIA_NIM,
            Provider.CEREBRAS,
            Provider.FREE_LLM_API,
            Provider.OPENROUTER,
            Provider.MISTRAL,
            Provider.GEMINI_FLASH,
            Provider.CLOUDFLARE,
        ]
    elif task_type == TaskType.HIGH_VOLUME:
        return [
            Provider.GEMINI_FLASH,
            Provider.FREE_LLM_API,
            Provider.OPENROUTER,
            Provider.NVIDIA_NIM,
            Provider.GROQ,
            Provider.MISTRAL,
            Provider.CEREBRAS,
            Provider.CLOUDFLARE,
        ]
    else:  # GENERAL
        return [
            Provider.FREE_LLM_API,
            Provider.NVIDIA_NIM,
            Provider.GROQ,
            Provider.GEMINI_FLASH,
            Provider.CEREBRAS,
            Provider.MISTRAL,
            Provider.OPENROUTER,
            Provider.CLOUDFLARE,
        ]


class BrainRouter:
    """Main brain router with automatic failover across providers."""

    def __init__(self):
        self.clients: Dict[Provider, ProviderClient] = {}
        self._initialized = False

    def _initialize_clients(self):
        """Lazy initialization of provider clients."""
        if self._initialized:
            return

        for provider, config in PROVIDER_CONFIGS.items():
            if config.enabled and os.getenv(config.api_key_env):
                client_class = CLIENT_CLASSES.get(provider)
                if client_class:
                    self.clients[provider] = client_class(config)
                    logger.info(f"Initialized provider: {provider.value}")

        self._initialized = True

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Execute completion with automatic failover."""
        self._initialize_clients()

        routing_order = get_routing_order(request.task_type)

        last_error = "No providers available"
        for provider in routing_order:
            if provider not in self.clients:
                continue

            client = self.clients[provider]
            logger.info(f"Trying provider: {provider.value}")

            response = await client.complete(request)
            if response.success:
                logger.info(f"Success with {provider.value} in {response.latency_ms}ms")
                return response
            else:
                last_error = response.error or "Unknown error"
                logger.warning(f"Provider {provider.value} failed: {last_error}")

        # All providers failed
        return CompletionResponse(
            content="",
            provider=Provider.FREE_LLM_API,
            model="",
            success=False,
            error=f"All providers failed. Last error: {last_error}"
        )

    async def close(self):
        """Close all provider clients."""
        for client in self.clients.values():
            await client.close()
        self.clients.clear()
        self._initialized = False


# Singleton instance
_router: Optional[BrainRouter] = None


def get_brain_router() -> BrainRouter:
    """Get the singleton brain router instance."""
    global _router
    if _router is None:
        _router = BrainRouter()
    return _router


async def complete(
    prompt: str,
    task_type: TaskType = TaskType.GENERAL,
    model: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    system_prompt: Optional[str] = None
) -> CompletionResponse:
    """Convenience function for completions."""
    router = get_brain_router()
    request = CompletionRequest(
        prompt=prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        task_type=task_type,
        system_prompt=system_prompt
    )
    return await router.complete(request)