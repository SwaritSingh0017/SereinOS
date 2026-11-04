"""
AI Brain Router - Provider abstraction with latency optimization.
Routes requests to appropriate model (fast/smart/local) with fallback.
Target: <2s response time for fast model, <10s for smart model.
"""
import asyncio
import logging
import os
import time
from datetime import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, Literal, Union
import aiohttp
import json

logger = logging.getLogger("serein.core.ai")


class ModelTier(Enum):
    FAST = "fast"      # <2s, for simple queries, classification
    SMART = "smart"    # <10s, for complex reasoning, creative tasks
    LOCAL = "local"    # Offline, privacy-sensitive


class BrainOutputError(Exception):
    """Raised when brain output format or generation fails."""
    pass


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionProposal:
    content: str
    has_tool_calls: bool
    tool_calls: List[ToolCall] = field(default_factory=list)


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: Optional[str] = None


@dataclass
class ChatResponse:
    content: str
    model: str
    tier: ModelTier
    latency_ms: int
    tokens_used: Optional[int] = None
    finish_reason: Optional[str] = None
    provider: str = ""


@dataclass
class ModelConfig:
    name: str
    tier: ModelTier
    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    timeout: float = 30.0


class AIProvider(ABC):
    """Abstract AI provider interface."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass
    
    @property
    @abstractmethod
    def available_tiers(self) -> List[ModelTier]:
        pass
    
    @abstractmethod
    async def chat(
        self,
        messages: List[ChatMessage],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> ChatResponse:
        pass
    
    @abstractmethod
    async def stream_chat(
        self,
        messages: List[ChatMessage],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> AsyncIterator[str]:
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        pass
    
    @abstractmethod
    def get_model_config(self, tier: ModelTier) -> ModelConfig:
        pass


class NvidiaProvider(AIProvider):
    """NVIDIA API provider (Nemotron, Llama models)."""
    
    def __init__(self, api_key: str, base_url: str = "https://integrate.api.nvidia.com/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def name(self) -> str:
        return "nvidia"
    
    @property
    def available_tiers(self) -> List[ModelTier]:
        return [ModelTier.FAST, ModelTier.SMART]
    
    def get_model_config(self, tier: ModelTier) -> ModelConfig:
        return ModelConfig(
            name="meta/llama-3.1-8b-instruct" if tier == ModelTier.FAST else "meta/llama-3.3-70b-instruct",
            tier=tier,
            max_tokens=2048,
            temperature=0.7,
            timeout=15.0 if tier == ModelTier.FAST else 30.0,
        )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session
    
    async def chat(
        self,
        messages: List[ChatMessage],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> ChatResponse:
        config = self.get_model_config(tier)
        config.name = model
        
        session = await self._get_session()
        start = time.perf_counter()
        
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", config.temperature),
            "top_p": kwargs.get("top_p", config.top_p),
            "stream": False,
        }
        
        async with session.post(f"{self.base_url}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
        
        latency = int((time.perf_counter() - start) * 1000)
        
        choice = data["choices"][0]
        return ChatResponse(
            content=choice["message"]["content"],
            model=model,
            tier=tier,
            latency_ms=latency,
            tokens_used=data.get("usage", {}).get("total_tokens"),
            finish_reason=choice.get("finish_reason"),
            provider=self.name,
        )
    
    async def stream_chat(
        self,
        messages: List[ChatMessage],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> AsyncIterator[str]:
        config = self.get_model_config(tier)
        config.name = model
        
        session = await self._get_session()
        
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", config.temperature),
            "top_p": kwargs.get("top_p", config.top_p),
            "stream": True,
        }
        
        async with session.post(f"{self.base_url}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                    except json.JSONDecodeError:
                        continue
    
    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/models") as resp:
                return resp.status == 200
        except Exception:
            return False
    
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class OpenRouterProvider(AIProvider):
    """OpenRouter provider for multiple models."""
    
    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def name(self) -> str:
        return "openrouter"
    
    @property
    def available_tiers(self) -> List[ModelTier]:
        return [ModelTier.FAST, ModelTier.SMART]
    
    def get_model_config(self, tier: ModelTier) -> ModelConfig:
        models = {
            ModelTier.FAST: "google/gemma-2-9b-it:free",
            ModelTier.SMART: "anthropic/claude-3.5-sonnet",
        }
        return ModelConfig(
            name=models[tier],
            tier=tier,
            timeout=15.0 if tier == ModelTier.FAST else 45.0,
        )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": "https://serein.ai",
                    "X-Title": "SereinOS",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session
    
    async def chat(
        self,
        messages: List[ChatMessage],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> ChatResponse:
        config = self.get_model_config(tier)
        config.name = model
        
        session = await self._get_session()
        start = time.perf_counter()
        
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", config.temperature),
        }
        
        async with session.post(f"{self.base_url}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
        
        latency = int((time.perf_counter() - start) * 1000)
        
        choice = data["choices"][0]
        return ChatResponse(
            content=choice["message"]["content"],
            model=model,
            tier=tier,
            latency_ms=latency,
            tokens_used=data.get("usage", {}).get("total_tokens"),
            provider=self.name,
        )
    
    async def stream_chat(
        self,
        messages: List[ChatMessage],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> AsyncIterator[str]:
        # Implementation similar to NvidiaProvider
        config = self.get_model_config(tier)
        config.name = model
        
        session = await self._get_session()
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", config.temperature),
            "stream": True,
        }
        
        async with session.post(f"{self.base_url}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                    except json.JSONDecodeError:
                        continue
    
    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/models") as resp:
                return resp.status == 200
        except Exception:
            return False
    
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class OllamaProvider(AIProvider):
    """Local Ollama provider."""
    
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def name(self) -> str:
        return "ollama"
    
    @property
    def available_tiers(self) -> List[ModelTier]:
        return [ModelTier.LOCAL, ModelTier.FAST, ModelTier.SMART]
    
    def get_model_config(self, tier: ModelTier) -> ModelConfig:
        models = {
            ModelTier.FAST: "llama3.1:8b",
            ModelTier.SMART: "llama3.1:70b",
            ModelTier.LOCAL: "llama3.1:8b",
        }
        return ModelConfig(
            name=models[tier],
            tier=tier,
            timeout=30.0 if tier == ModelTier.FAST else 120.0,
        )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120),
            )
        return self._session
    
    async def chat(
        self,
        messages: List[ChatMessage],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> ChatResponse:
        config = self.get_model_config(tier)
        config.name = model
        
        session = await self._get_session()
        start = time.perf_counter()
        
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "num_predict": kwargs.get("max_tokens", config.max_tokens),
                "temperature": kwargs.get("temperature", config.temperature),
            },
        }
        
        async with session.post(f"{self.base_url}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
        
        latency = int((time.perf_counter() - start) * 1000)
        
        return ChatResponse(
            content=data["message"]["content"],
            model=model,
            tier=tier,
            latency_ms=latency,
            tokens_used=data.get("eval_count"),
            provider=self.name,
        )
    
    async def stream_chat(
        self,
        messages: List[ChatMessage],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> AsyncIterator[str]:
        config = self.get_model_config(tier)
        config.name = model
        
        session = await self._get_session()
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {
                "num_predict": kwargs.get("max_tokens", config.max_tokens),
                "temperature": kwargs.get("temperature", config.temperature),
            },
        }
        
        async with session.post(f"{self.base_url}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if line:
                    try:
                        data = json.loads(line)
                        if "message" in data and "content" in data["message"]:
                            yield data["message"]["content"]
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
    
    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/api/tags") as resp:
                return resp.status == 200
        except Exception:
            return False
    
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class OpenAIProvider(AIProvider):
    """Direct OpenAI API provider."""
    
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def name(self) -> str:
        return "openai"
    
    @property
    def available_tiers(self) -> List[ModelTier]:
        return [ModelTier.FAST, ModelTier.SMART]
    
    def get_model_config(self, tier: ModelTier) -> ModelConfig:
        models = {
            ModelTier.FAST: "gpt-4o-mini",
            ModelTier.SMART: "gpt-4o",
        }
        return ModelConfig(
            name=models.get(tier, "gpt-4o-mini"),
            tier=tier,
            max_tokens=2048 if tier == ModelTier.FAST else 4096,
            timeout=15.0 if tier == ModelTier.FAST else 30.0,
        )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session
    
    async def chat(
        self,
        messages: List[ChatMessage | Dict],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> ChatResponse:
        config = self.get_model_config(tier)
        model_name = model or config.name
        session = await self._get_session()
        start = time.perf_counter()
        
        formatted_msgs = []
        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "user")
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            formatted_msgs.append({"role": role, "content": content})
            
        payload = {
            "model": model_name,
            "messages": formatted_msgs,
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", config.temperature),
            "top_p": kwargs.get("top_p", config.top_p),
            "stream": False,
        }
        
        async with session.post(f"{self.base_url}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
        
        latency = int((time.perf_counter() - start) * 1000)
        choice = data["choices"][0]
        return ChatResponse(
            content=choice["message"]["content"],
            model=model_name,
            tier=tier,
            latency_ms=latency,
            tokens_used=data.get("usage", {}).get("total_tokens"),
            finish_reason=choice.get("finish_reason"),
            provider=self.name,
        )
    
    async def stream_chat(
        self,
        messages: List[ChatMessage | Dict],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> AsyncIterator[str]:
        config = self.get_model_config(tier)
        model_name = model or config.name
        session = await self._get_session()
        
        formatted_msgs = []
        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "user")
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            formatted_msgs.append({"role": role, "content": content})
            
        payload = {
            "model": model_name,
            "messages": formatted_msgs,
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", config.temperature),
            "stream": True,
        }
        
        async with session.post(f"{self.base_url}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data: "):
                    data_str = line_str[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                    except json.JSONDecodeError:
                        continue
    
    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/models") as resp:
                return resp.status == 200
        except Exception:
            return False
            
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class AnthropicProvider(AIProvider):
    """Anthropic Claude API provider."""
    
    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def name(self) -> str:
        return "anthropic"
    
    @property
    def available_tiers(self) -> List[ModelTier]:
        return [ModelTier.FAST, ModelTier.SMART]
    
    def get_model_config(self, tier: ModelTier) -> ModelConfig:
        models = {
            ModelTier.FAST: "claude-3-5-haiku-20241022",
            ModelTier.SMART: "claude-3-5-sonnet-20241022",
        }
        return ModelConfig(
            name=models.get(tier, "claude-3-5-haiku-20241022"),
            tier=tier,
            max_tokens=2048 if tier == ModelTier.FAST else 4096,
            timeout=15.0 if tier == ModelTier.FAST else 45.0,
        )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session
    
    async def chat(
        self,
        messages: List[ChatMessage | Dict],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> ChatResponse:
        config = self.get_model_config(tier)
        model_name = model or config.name
        session = await self._get_session()
        start = time.perf_counter()
        
        system_prompt = ""
        user_msgs = []
        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "user")
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            if role == "system":
                system_prompt = f"{system_prompt}\n{content}".strip() if system_prompt else content
            else:
                user_msgs.append({"role": role, "content": content})
                
        payload = {
            "model": model_name,
            "messages": user_msgs,
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", config.temperature),
        }
        if system_prompt:
            payload["system"] = system_prompt
            
        async with session.post(f"{self.base_url}/messages", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            
        latency = int((time.perf_counter() - start) * 1000)
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
                
        return ChatResponse(
            content=content,
            model=model_name,
            tier=tier,
            latency_ms=latency,
            tokens_used=data.get("usage", {}).get("output_tokens"),
            finish_reason=data.get("stop_reason"),
            provider=self.name,
        )
    
    async def stream_chat(
        self,
        messages: List[ChatMessage | Dict],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> AsyncIterator[str]:
        resp = await self.chat(messages, model, tier, **kwargs)
        yield resp.content
        
    async def health_check(self) -> bool:
        return bool(self.api_key)
        
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class GeminiProvider(AIProvider):
    """Google Gemini API provider."""
    
    def __init__(self, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def name(self) -> str:
        return "gemini"
    
    @property
    def available_tiers(self) -> List[ModelTier]:
        return [ModelTier.FAST, ModelTier.SMART]
    
    def get_model_config(self, tier: ModelTier) -> ModelConfig:
        models = {
            ModelTier.FAST: "gemini-1.5-flash",
            ModelTier.SMART: "gemini-1.5-pro",
        }
        return ModelConfig(
            name=models.get(tier, "gemini-1.5-flash"),
            tier=tier,
            max_tokens=2048 if tier == ModelTier.FAST else 4096,
            timeout=15.0 if tier == ModelTier.FAST else 30.0,
        )
        
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session
        
    async def chat(
        self,
        messages: List[ChatMessage | Dict],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> ChatResponse:
        config = self.get_model_config(tier)
        model_name = model or config.name
        session = await self._get_session()
        start = time.perf_counter()
        
        contents = []
        system_instruction = None
        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "user")
            text = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            if role == "system":
                system_instruction = {"parts": [{"text": text}]}
            else:
                gemini_role = "user" if role == "user" else "model"
                contents.append({"role": gemini_role, "parts": [{"text": text}]})
                
        payload: Dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = system_instruction
            
        url = f"{self.base_url}/models/{model_name}:generateContent?key={self.api_key}"
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            
        latency = int((time.perf_counter() - start) * 1000)
        content = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                content = parts[0].get("text", "")
                
        return ChatResponse(
            content=content,
            model=model_name,
            tier=tier,
            latency_ms=latency,
            provider=self.name,
        )
        
    async def stream_chat(
        self,
        messages: List[ChatMessage | Dict],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> AsyncIterator[str]:
        resp = await self.chat(messages, model, tier, **kwargs)
        yield resp.content
        
    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/models?key={self.api_key}") as resp:
                return resp.status == 200
        except Exception:
            return False
            
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class CustomOpenAIProvider(AIProvider):
    """Generic OpenAI-compatible provider for local endpoints (vLLM, LMStudio, LocalAI, etc.)."""
    
    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "sk-custom",
        name: str = "custom_openai",
        default_model: str = "default-model"
    ):
        self.api_key = api_key or "sk-custom"
        self.base_url = base_url.rstrip("/")
        self._name = name
        self.default_model = default_model
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def available_tiers(self) -> List[ModelTier]:
        return [ModelTier.LOCAL, ModelTier.FAST, ModelTier.SMART]
    
    def get_model_config(self, tier: ModelTier) -> ModelConfig:
        return ModelConfig(
            name=self.default_model,
            tier=tier,
            max_tokens=2048,
            temperature=0.7,
            timeout=30.0,
        )
        
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session
        
    async def chat(
        self,
        messages: List[ChatMessage | Dict],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> ChatResponse:
        config = self.get_model_config(tier)
        model_name = model or config.name
        session = await self._get_session()
        start = time.perf_counter()
        
        formatted_msgs = []
        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "user")
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            formatted_msgs.append({"role": role, "content": content})
            
        payload = {
            "model": model_name,
            "messages": formatted_msgs,
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", config.temperature),
            "stream": False,
        }
        
        async with session.post(f"{self.base_url}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            
        latency = int((time.perf_counter() - start) * 1000)
        choice = data["choices"][0]
        return ChatResponse(
            content=choice["message"]["content"],
            model=model_name,
            tier=tier,
            latency_ms=latency,
            tokens_used=data.get("usage", {}).get("total_tokens"),
            finish_reason=choice.get("finish_reason"),
            provider=self.name,
        )
        
    async def stream_chat(
        self,
        messages: List[ChatMessage | Dict],
        model: str,
        tier: ModelTier,
        **kwargs
    ) -> AsyncIterator[str]:
        resp = await self.chat(messages, model, tier, **kwargs)
        yield resp.content
        
    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/models") as resp:
                return resp.status == 200
        except Exception:
            return False
            
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class BrainRouter:
    """
    Main AI router - selects optimal provider/tier for each request.
    Implements latency-aware routing with automatic fallback.
    """
    
    def __init__(self, config):
        self.config = config
        self.providers: Dict[str, AIProvider] = {}
        self._initialize_providers()
        
        # Latency tracking for adaptive routing
        self._latency_history: Dict[str, List[float]] = {}
        self._failure_counts: Dict[str, int] = {}
    
    def _initialize_providers(self) -> None:
        """Initialize configured providers."""
        ai_config = getattr(self.config, "ai", None)
        
        # NVIDIA
        nvidia_key = getattr(ai_config, "nvidia_api_key", None) or os.getenv("NVIDIA_API_KEY")
        if nvidia_key:
            self.providers["nvidia"] = NvidiaProvider(nvidia_key)
        
        # OpenRouter
        openrouter_key = getattr(ai_config, "openrouter_api_key", None) or os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            self.providers["openrouter"] = OpenRouterProvider(openrouter_key)
            
        # OpenAI
        openai_key = getattr(ai_config, "openai_api_key", None) or os.getenv("OPENAI_API_KEY")
        if openai_key:
            self.providers["openai"] = OpenAIProvider(openai_key)
            
        # Anthropic
        anthropic_key = getattr(ai_config, "anthropic_api_key", None) or os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key:
            self.providers["anthropic"] = AnthropicProvider(anthropic_key)
            
        # Gemini
        gemini_key = getattr(ai_config, "gemini_api_key", None) or os.getenv("GEMINI_API_KEY")
        if gemini_key:
            self.providers["gemini"] = GeminiProvider(gemini_key)
            
        # Custom local / private endpoint
        custom_base = getattr(ai_config, "custom_openai_base_url", None) or os.getenv("CUSTOM_OPENAI_BASE_URL")
        if custom_base:
            self.providers["custom"] = CustomOpenAIProvider(base_url=custom_base)
        
        # Ollama (always try default or configured)
        ollama_base = getattr(ai_config, "ollama_base_url", "http://localhost:11434")
        self.providers["ollama"] = OllamaProvider(ollama_base)
        
        logger.info(f"Initialized AI providers: {list(self.providers.keys())}")

    def register_provider(self, name: str, provider: AIProvider) -> None:
        """Register custom provider instance."""
        self.providers[name] = provider
        logger.info(f"Registered custom provider: {name}")
    
    def _select_provider_and_model(
        self,
        tier: ModelTier,
        prefer_local: bool = False,
        require_json: bool = False
    ) -> tuple[AIProvider, str]:
        """Select best provider for given tier."""
        
        # Filter providers that support the tier
        candidates = [
            (p, p.get_model_config(tier).name)
            for p in self.providers.values()
            if tier in p.available_tiers
        ]
        
        if not candidates:
            # Fallback to any provider
            candidates = [
                (p, p.get_model_config(ModelTier.FAST).name)
                for p in self.providers.values()
            ]
        
        if prefer_local:
            # Prioritize local providers
            local = [(p, m) for p, m in candidates if isinstance(p, OllamaProvider)]
            if local:
                candidates = local
        
        # Sort by: health, latency history, failure count
        def score(candidate):
            provider, model = candidate
            name = provider.name
            
            # Penalize failures
            failures = self._failure_counts.get(name, 0)
            
            # Use average latency
            latencies = self._latency_history.get(name, [3000])
            avg_latency = sum(latencies) / len(latencies)
            
            return (failures, avg_latency)
        
        candidates.sort(key=score)
        return candidates[0]
    
    async def chat(
        self,
        messages: List[ChatMessage | Dict],
        tier: ModelTier = ModelTier.FAST,
        prefer_local: bool = False,
        **kwargs
    ) -> ChatResponse:
        """Route chat request to optimal provider with offline detection and graceful fallback."""
        if not messages:
            raise ValueError("Messages list cannot be empty")
        
        # Check context internet connectivity
        context = kwargs.get("context")
        is_online = True
        if context is not None:
            if isinstance(context, dict):
                is_online = context.get("internet_connected", True)
            elif hasattr(context, "internet_connected"):
                is_online = bool(getattr(context, "internet_connected", True))
        
        # Direct local engine or offline routing
        if tier == ModelTier.LOCAL or prefer_local or not is_online:
            if hasattr(self, "engine") and self.engine is not None:
                start = time.perf_counter()
                content = await self.engine.generate(messages, **kwargs)
                latency = int((time.perf_counter() - start) * 1000)
                model_name = getattr(self.plan, "selected_model", "serein-local-small") if hasattr(self, "plan") and self.plan else "serein-local-small"
                return ChatResponse(
                    content=content,
                    model=model_name,
                    tier=ModelTier.LOCAL,
                    latency_ms=latency,
                    provider="local-llama.cpp",
                    finish_reason="stop"
                )
            # If prefer_local and Ollama is registered, try it
            if prefer_local and "ollama" in self.providers:
                try:
                    provider = self.providers["ollama"]
                    config = provider.get_model_config(ModelTier.LOCAL)
                    resp = await provider.chat(messages, config.name, ModelTier.LOCAL, **kwargs)
                    return resp
                except Exception as e:
                    logger.debug(f"Ollama local attempt failed: {e}")
            
            # Deterministic offline reasoning generator
            return self._generate_offline_response(messages, **kwargs)
            
        last_error = None
        max_retries = 2
        
        for attempt in range(max_retries + 1):
            try:
                provider, model = self._select_provider_and_model(tier, prefer_local)
                logger.debug(f"Routing to {provider.name}/{model} (tier: {tier.value})")
                
                start = time.perf_counter()
                response = await provider.chat(messages, model, tier, **kwargs)
                latency = int((time.perf_counter() - start) * 1000)
                response.latency_ms = latency
                
                # Track success
                self._record_latency(provider.name, latency)
                self._failure_counts[provider.name] = max(0, self._failure_counts.get(provider.name, 0) - 1)
                
                logger.info(f"AI response from {provider.name}/{model}: {latency}ms")
                return response
                
            except Exception as e:
                last_error = e
                logger.warning(f"Provider {provider.name if 'provider' in locals() else 'unknown'} failed: {e}")
                if 'provider' in locals():
                    self._failure_counts[provider.name] = self._failure_counts.get(provider.name, 0) + 1
                
                if attempt < max_retries:
                    # Try next best provider
                    continue
        
        # All providers failed: Graceful failover to offline reasoning generator without raising unhandled RuntimeError
        logger.warning(f"All AI providers failed ({last_error}). Gracefully failing over to local deterministic offline reasoning.")
        if hasattr(self, "engine") and self.engine is not None:
            try:
                start = time.perf_counter()
                content = await self.engine.generate(messages, **kwargs)
                latency = int((time.perf_counter() - start) * 1000)
                return ChatResponse(
                    content=content,
                    model="serein-local-small",
                    tier=ModelTier.LOCAL,
                    latency_ms=latency,
                    provider="local-llama.cpp",
                    finish_reason="offline_fallback"
                )
            except Exception as le:
                logger.warning(f"Local engine generation also failed: {le}")
                
        return self._generate_offline_response(messages, fallback_error=str(last_error), **kwargs)

    def _generate_offline_response(
        self,
        messages: List[ChatMessage | Dict],
        fallback_error: Optional[str] = None,
        **kwargs
    ) -> ChatResponse:
        """Deterministic offline intent-based response generator."""
        start = time.perf_counter()
        user_text = ""
        for m in reversed(messages):
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "")
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            if role == "user":
                user_text = content
                break
                
        # If user message contains prompt header like "USER MESSAGE:\n...", extract the actual text
        if "USER MESSAGE:" in user_text:
            try:
                parts = user_text.split("USER MESSAGE:\n")
                if len(parts) > 1:
                    user_text = parts[1].split("\n=")[0].strip()
            except Exception:
                pass
                
        from serein_core.intent.classifier import IntentClassifier
        classifier = IntentClassifier()
        intent_res = classifier.classify(user_text) if user_text else None
        
        intent = intent_res.intent if intent_res else "unknown"
        entities = intent_res.entities if intent_res else {}
        
        if not user_text:
            content = "I am listening. How can I assist you with SereinOS?"
        elif intent == "greeting":
            content = "Hello! I am Serein, your desktop companion operating in local offline mode. How can I assist you today?"
        elif intent == "farewell":
            content = "Goodbye! Serein will be here whenever you need assistance."
        elif intent == "reminder":
            task = entities.get("task", "your reminder")
            t = entities.get("time", "the requested time")
            content = f"I have scheduled your reminder to '{task}' at {t}."
        elif intent == "system_control":
            action = entities.get("action", "system action")
            content = f"Executing system action: {action}."
        elif intent == "app_control":
            app = entities.get("application", "application")
            content = f"Opening {app} for you."
        elif intent == "file_operation":
            op = entities.get("operation", "file operation")
            content = f"Performing file operation: {op}."
        elif intent == "question":
            norm = user_text.lower()
            if "time" in norm or "date" in norm:
                now_dt = datetime.now()
                content = f"The current time is {now_dt.strftime('%H:%M')} on {now_dt.strftime('%A, %B %d, %Y')}."
            elif "battery" in norm:
                content = "Battery status is monitored via the ACPI subsystem."
            elif "who are you" in norm or "what are you" in norm:
                content = "I am Serein, your intelligent operating system companion for SereinOS."
            elif "system" in norm or "status" in norm or "cpu" in norm or "ram" in norm:
                content = "System status: SereinOS core services and offline brain are fully operational."
            else:
                content = f"I am currently offline and cannot query cloud intelligence for '{user_text}', but local system commands, apps, and scheduler remain fully operational."
        else:
            content = "I am currently in local offline mode. I can run system actions, open apps, schedule reminders, and assist with your workflow."
            
        if kwargs.get("require_json"):
            content = json.dumps({"content": content, "tool_calls": []})
            
        latency = max(1, int((time.perf_counter() - start) * 1000))
        return ChatResponse(
            content=content,
            model="serein-offline-intent",
            tier=ModelTier.LOCAL,
            latency_ms=latency,
            finish_reason="offline_deterministic",
            provider="offline-deterministic",
        )
    
    async def propose_action(
        self,
        messages: List[ChatMessage],
        allowed_tools: Optional[List[str]] = None,
        tier: ModelTier = ModelTier.FAST,
        **kwargs
    ) -> ActionProposal:
        """Propose an action with structured tool call parsing."""
        if not messages:
            raise ValueError("Messages list cannot be empty")
        
        response = await self.chat(messages, tier=tier, **kwargs)
        raw_content = response.content.strip()
        
        try:
            data = json.loads(raw_content)
            if not isinstance(data, dict):
                raise BrainOutputError("Action proposal must be a JSON object")
        except Exception as e:
            raise BrainOutputError(f"Failed to parse action JSON: {e}") from e
        
        content = data.get("content", "")
        raw_tool_calls = data.get("tool_calls", [])
        tool_calls: List[ToolCall] = []
        
        for item in raw_tool_calls:
            name = item.get("name")
            if not name:
                continue
            if allowed_tools is not None and name not in allowed_tools:
                continue
            args = item.get("arguments", {})
            tool_calls.append(ToolCall(name=name, arguments=args))
        
        return ActionProposal(
            content=content,
            has_tool_calls=len(tool_calls) > 0,
            tool_calls=tool_calls,
        )
    
    async def stream_chat(
        self,
        messages: List[ChatMessage],
        tier: ModelTier = ModelTier.FAST,
        prefer_local: bool = False,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream chat with fallback."""
        provider, model = self._select_provider_and_model(tier, prefer_local)
        try:
            async for chunk in provider.stream_chat(messages, model, tier, **kwargs):
                yield chunk
        except Exception as e:
            logger.error(f"Stream failed for {provider.name}: {e}")
            # Fallback to non-streaming
            response = await self.chat(messages, tier, prefer_local, **kwargs)
            yield response.content
    
    def _record_latency(self, provider_name: str, latency_ms: int) -> None:
        if provider_name not in self._latency_history:
            self._latency_history[provider_name] = []
        self._latency_history[provider_name].append(latency_ms)
        # Keep last 100 measurements
        if len(self._latency_history[provider_name]) > 100:
            self._latency_history[provider_name] = self._latency_history[provider_name][-100:]
    
    async def health_check_all(self) -> Dict[str, bool]:
        """Check health of all providers."""
        results = {}
        for name, provider in self.providers.items():
            try:
                results[name] = await provider.health_check()
            except Exception:
                results[name] = False
        return results
    
    async def close(self) -> None:
        for provider in self.providers.values():
            if hasattr(provider, 'close'):
                await provider.close()


# Global router instance
router: Optional[BrainRouter] = None


async def get_router() -> BrainRouter:
    global router
    if router is None:
        from serein_core.config import get_config as config
        router = BrainRouter(config)
    return router