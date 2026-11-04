"""Local GGUF inference adapter.

`llama-cpp-python` is deliberately an optional runtime dependency. Serein Core
can therefore be installed and tested without a model or GPU backend.
No network/API calls are made by this module.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from serein_core.model_manager import RuntimePlan


@dataclass(frozen=True)
class InferenceConfig:
    model_path: Path
    context_tokens: int
    threads: int
    gpu_layers: int
    temperature: float = 0.7
    top_p: float = 0.9
    max_output_tokens: int = 1024

    @classmethod
    def from_plan(cls, plan: RuntimePlan, model_path: str | Path) -> "InferenceConfig":
        return cls(
            model_path=Path(model_path).expanduser(),
            context_tokens=plan.max_context_tokens,
            threads=plan.threads,
            gpu_layers=plan.gpu_layers,
            max_output_tokens=plan.max_output_tokens,
        )


class LocalInferenceEngine:
    """Small async wrapper around llama.cpp's Python bindings."""

    def __init__(self, config: InferenceConfig):
        self.config = config
        self._llama: Any = None

    @property
    def available(self) -> bool:
        return self.config.model_path.is_file()

    def _load(self) -> Any:
        if self._llama is not None:
            return self._llama
        if not self.config.model_path.is_file():
            raise FileNotFoundError(
                f"Local model not found: {self.config.model_path}"
            )
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise RuntimeError(
                "llama-cpp-python is not installed. Install it separately for local GGUF inference."
            ) from exc

        self._llama = Llama(
            model_path=str(self.config.model_path),
            n_ctx=self.config.context_tokens,
            n_threads=self.config.threads,
            n_gpu_layers=self.config.gpu_layers,
            verbose=False,
        )
        return self._llama

    async def stream(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ):
        """Stream generated text from llama.cpp without blocking the event loop."""
        model = self._load()
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()

        loop = asyncio.get_running_loop()
        # Capture the loop before moving to the worker thread.
        def worker_with_loop() -> None:
            try:
                iterator = model.create_chat_completion(
                    messages=list(messages),
                    max_tokens=max_tokens or self.config.max_output_tokens,
                    temperature=self.config.temperature if temperature is None else temperature,
                    top_p=self.config.top_p if top_p is None else top_p,
                    stream=True,
                )
                for item in iterator:
                    choices = item.get("choices", [])
                    if choices:
                        content = choices[0].get("delta", {}).get("content")
                        if content:
                            asyncio.run_coroutine_threadsafe(queue.put(content), loop).result()
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(sentinel), loop).result()

        worker_task = asyncio.create_task(asyncio.to_thread(worker_with_loop))
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                await worker_task
                raise item
            yield str(item)
        await worker_task

    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> str:
        """Generate a response without blocking the core event loop."""
        model = self._load()
        result = await asyncio.to_thread(
            model.create_chat_completion,
            messages=list(messages),
            max_tokens=max_tokens or self.config.max_output_tokens,
            temperature=self.config.temperature if temperature is None else temperature,
            top_p=self.config.top_p if top_p is None else top_p,
        )
        return str(result["choices"][0]["message"]["content"]).strip()

    def close(self) -> None:
        self._llama = None


def resolve_model_path(model_id: str, default_dir: str | Path | None = None) -> Path:
    """Resolve a model profile to a local GGUF path using environment/config."""
    env_name = f"SEREIN_MODEL_{model_id.upper().replace('-', '_')}"
    configured = os.getenv(env_name)
    if configured:
        return Path(configured).expanduser()

    base = Path(default_dir or os.getenv("SEREIN_MODELS_DIR", "~/.local/share/serein/models"))
    filenames = {
        "serein-local-tiny": "serein-tiny.gguf",
        "serein-local-small": "serein-small.gguf",
        "serein-local-medium": "serein-medium.gguf",
        "serein-local-power": "serein-power.gguf",
    }
    return base.expanduser() / filenames.get(model_id, f"{model_id}.gguf")


def create_local_engine(plan: RuntimePlan, model_path: str | Path | None = None) -> LocalInferenceEngine:
    path = Path(model_path).expanduser() if model_path else resolve_model_path(plan.model_id)
    return LocalInferenceEngine(InferenceConfig.from_plan(plan, path))
