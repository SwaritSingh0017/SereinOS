"""
AI Provider System - Supports multiple LLM providers with fallback.
Includes Nvidia, Ollama, OpenRouter, and a Mock provider for testing.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import requests
import os
from dotenv import load_dotenv

load_dotenv()


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""
    
    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Generate a response from the LLM."""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is available/configured."""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass


class NvidiaProvider(BaseProvider):
    """NVIDIA API provider."""
    
    def __init__(self, model: str = None, api_key: str = None, 
                 base_url: str = None, temperature: float = 0.7, 
                 max_tokens: int = 500, timeout: int = 60):
        self.model = model or "meta/llama-3.3-70b-instruct"
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY")
        self.base_url = base_url or "https://integrate.api.nvidia.com/v1/chat/completions"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
    
    def is_available(self) -> bool:
        return bool(self.api_key)
    
    @property
    def name(self) -> str:
        return "nvidia"
    
    def generate_response(self, prompt: str) -> str:
        if not self.api_key:
            raise ValueError("NVIDIA_API_KEY not configured")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        
        response = requests.post(
            self.base_url,
            headers=headers,
            json=payload,
            timeout=self.timeout
        )
        
        response.raise_for_status()
        data = response.json()
        
        return data["choices"][0]["message"]["content"]


class OllamaProvider(BaseProvider):
    """Ollama local provider."""
    
    def __init__(self, model: str = None, base_url: str = None,
                 temperature: float = 0.7, max_tokens: int = 500, timeout: int = 60):
        self.model = model or "llama3.1:8b"
        self.base_url = base_url or "http://localhost:11434/api/generate"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
    
    def is_available(self) -> bool:
        try:
            response = requests.get(self.base_url.replace("/api/generate", "/api/tags"), timeout=2)
            return response.status_code == 200
        except:
            return False
    
    @property
    def name(self) -> str:
        return "ollama"
    
    def generate_response(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            }
        }
        
        response = requests.post(self.base_url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        
        return data.get("response", "")


class OpenRouterProvider(BaseProvider):
    """OpenRouter API provider."""
    
    def __init__(self, model: str = None, api_key: str = None,
                 base_url: str = None, temperature: float = 0.7,
                 max_tokens: int = 500, timeout: int = 60):
        self.model = model or "meta-llama/llama-3.3-70b-instruct"
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.base_url = base_url or "https://openrouter.ai/api/v1/chat/completions"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
    
    def is_available(self) -> bool:
        return bool(self.api_key)
    
    @property
    def name(self) -> str:
        return "openrouter"
    
    def generate_response(self, prompt: str) -> str:
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/serein-ai",
            "X-Title": "SereinOS"
        }
        
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        
        response = requests.post(self.base_url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        
        return data["choices"][0]["message"]["content"]


class MockProvider(BaseProvider):
    """Mock provider for testing without API keys."""
    
    def __init__(self, model: str = "mock", **kwargs):
        self.model = model
        self._responses = [
            "Hello! I'm Serein, your AI companion. How can I help you today?",
            "That's interesting! Tell me more about that.",
            "I understand. Is there something specific you'd like to work on?",
            "Good to hear from you! What's on your mind?",
            "I'm doing well, thank you for asking. How are you doing?",
            "That sounds like a great project! I'd love to help.",
            "I remember you mentioned that before. How's it going?",
            "That's a thoughtful question. Let me think...",
            "I appreciate you sharing that with me.",
            "Is there anything else you'd like to talk about?"
        ]
        self._index = 0
    
    def is_available(self) -> bool:
        return True
    
    @property
    def name(self) -> str:
        return "mock"
    
    def generate_response(self, prompt: str) -> str:
        # Simple keyword-based responses for more realistic testing
        prompt_lower = prompt.lower()
        
        if "hello" in prompt_lower or "hi " in prompt_lower:
            return "Hello there! It's good to see you. How are you doing today?"
        
        if "how are you" in prompt_lower:
            return "I'm doing well, thank you for asking! I'm curious, how are things going with you?"
        
        if "name" in prompt_lower and ("my" in prompt_lower or "i'm" in prompt_lower or "i am" in prompt_lower):
            return "Nice to meet you! I'll remember that. Is there something you'd like to work on together?"
        
        if "bye" in prompt_lower or "goodbye" in prompt_lower:
            return "Goodbye for now! I'll be here whenever you need me. Take care!"
        
        if "thank" in prompt_lower:
            return "You're welcome! I'm happy to help."
        
        if "help" in prompt_lower:
            return "I'd be glad to help! What do you need assistance with?"
        
        if "remember" in prompt_lower or "memory" in prompt_lower:
            return "I'll make a note of that. My memory is one of the things that makes me different - I'm designed to remember what matters to you over time."
        
        # Cycle through default responses
        response = self._responses[self._index % len(self._responses)]
        self._index += 1
        return response


class ProviderManager:
    """Manages multiple providers with automatic fallback."""
    
    def __init__(self, config=None):
        self.providers: list[BaseProvider] = []
        self.current_provider: Optional[BaseProvider] = None
        self.config = config
        self._initialize_providers()
    
    def _initialize_providers(self) -> None:
        """Initialize providers based on config."""
        # Primary provider from config
        if self.config and hasattr(self.config, 'llm'):
            llm_config = self.config.llm
            
            if llm_config.provider == "nvidia":
                self.add_provider(NvidiaProvider(
                    model=llm_config.model,
                    api_key=llm_config.api_key,
                    base_url=llm_config.base_url,
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                    timeout=llm_config.timeout
                ))
            elif llm_config.provider == "ollama":
                self.add_provider(OllamaProvider(
                    model=llm_config.model,
                    base_url=llm_config.base_url,
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                    timeout=llm_config.timeout
                ))
            elif llm_config.provider == "openrouter":
                self.add_provider(OpenRouterProvider(
                    model=llm_config.model,
                    api_key=llm_config.api_key,
                    base_url=llm_config.base_url,
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                    timeout=llm_config.timeout
                ))
        
        # Always add fallbacks
        # Try Ollama if available (local, free)
        ollama = OllamaProvider()
        if ollama.is_available():
            self.add_provider(ollama)
        
        # Try OpenRouter
        if os.getenv("OPENROUTER_API_KEY"):
            self.add_provider(OpenRouterProvider())
        
        # Try Nvidia
        if os.getenv("NVIDIA_API_KEY"):
            self.add_provider(NvidiaProvider())
        
        # Always add mock as last resort
        self.add_provider(MockProvider())
    
    def add_provider(self, provider: BaseProvider) -> None:
        """Add a provider to the chain."""
        self.providers.append(provider)
    
    def get_available_provider(self) -> BaseProvider:
        """Get the first available provider."""
        for provider in self.providers:
            if provider.is_available():
                return provider
        return MockProvider()  # Should never reach here
    
    def generate_response(self, prompt: str) -> str:
        """Generate response with automatic fallback."""
        last_error = None
        
        for provider in self.providers:
            if not provider.is_available():
                continue
            
            try:
                response = provider.generate_response(prompt)
                self.current_provider = provider
                return response
            except Exception as e:
                last_error = e
                print(f"[Provider] {provider.name} failed: {e}")
                continue
        
        # All providers failed, use mock
        print(f"[Provider] All providers failed, using mock. Last error: {last_error}")
        mock = MockProvider()
        self.current_provider = mock
        return mock.generate_response(prompt)
    
    def get_current_provider_name(self) -> str:
        """Get name of current provider."""
        return self.current_provider.name if self.current_provider else "none"


# Backwards compatibility
def create_provider(config=None) -> ProviderManager:
    """Factory function to create provider manager."""
    return ProviderManager(config)