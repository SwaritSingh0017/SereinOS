"""Hardware-adaptive local model selection.

The manager never calls a cloud API and never downloads a model. It selects a
runtime profile from locally configured capabilities. Actual GGUF files are
resolved by :mod:`serein_core.inference`.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

from serein_core.hardware import HardwareProfile, get_hardware_profile


@dataclass(frozen=True)
class LocalModelProfile:
    model_id: str
    display_name: str
    family: str
    filename: str
    min_ram_gb: float
    recommended_ram_gb: float
    min_threads: int
    vram_gb: float
    context_tokens: int
    supports_tools: bool
    supports_vision: bool = False

    @property
    def configured_path(self) -> Optional[Path]:
        value = os.getenv(f"SEREIN_MODEL_{self.model_id.upper().replace('-', '_')}")
        return Path(value).expanduser() if value else None


DEFAULT_LOCAL_MODELS = (
    LocalModelProfile(
        "serein-local-tiny", "Serein Tiny Local", "gguf",
        "serein-tiny.gguf", 4, 6, 2, 0, 4096, True, False
    ),
    LocalModelProfile(
        "serein-local-small", "Serein Small Local", "gguf",
        "serein-small.gguf", 8, 12, 4, 0, 8192, True, False
    ),
    LocalModelProfile(
        "serein-local-medium", "Serein Medium Local", "gguf",
        "serein-medium.gguf", 16, 24, 8, 0, 16384, True, False
    ),
    LocalModelProfile(
        "serein-local-power", "Serein Power Local", "gguf",
        "serein-power.gguf", 32, 48, 12, 8, 32768, True, True
    ),
)


@dataclass(frozen=True)
class RuntimePlan:
    hardware_tier: str
    model_id: str
    max_context_tokens: int
    max_output_tokens: int
    threads: int
    gpu_layers: int
    enable_vision: bool
    enable_complex_agents: bool
    concurrency: int

    def to_dict(self) -> dict:
        return asdict(self)


class LocalModelManager:
    def __init__(
        self,
        hardware: Optional[HardwareProfile] = None,
        models: Iterable[LocalModelProfile] = DEFAULT_LOCAL_MODELS,
    ):
        self.hardware = hardware or get_hardware_profile()
        self.models = tuple(models)
        if not self.models:
            raise ValueError("At least one local model profile is required.")

    def _eligible(self) -> list[LocalModelProfile]:
        h = self.hardware
        return [
            model for model in self.models
            if h.ram_gb >= model.min_ram_gb and h.cpu_threads >= model.min_threads
            and (model.vram_gb == 0 or h.gpu_vram_gb >= model.vram_gb)
        ]

    def select_model(self) -> LocalModelProfile:
        eligible = self._eligible()
        if not eligible:
            return min(self.models, key=lambda m: (m.min_ram_gb, m.min_threads, m.vram_gb))

        return max(
            eligible,
            key=lambda m: (m.min_ram_gb, m.min_threads, m.context_tokens),
        )

    def build_plan(self) -> RuntimePlan:
        h = self.hardware
        model = self.select_model()

        thread_cap = {"minimum": 2, "standard": 6}.get(h.tier, max(1, h.cpu_threads - 2))
        threads = max(1, min(h.cpu_threads, thread_cap))

        gpu_layers = (
            -1
            if model.vram_gb > 0
            and h.gpu_vram_gb >= model.vram_gb
            and (h.has_cuda or h.has_rocm)
            else 0
        )

        context = min(model.context_tokens, 4096) if h.tier == "minimum" else model.context_tokens
        output = {"minimum": 1024, "standard": 2048, "power": 4096}.get(h.tier, 1024)
        concurrency = {"minimum": 1, "standard": 2, "power": 4}.get(h.tier, 1)

        return RuntimePlan(
            hardware_tier=h.tier,
            model_id=model.model_id,
            max_context_tokens=context,
            max_output_tokens=output,
            threads=threads,
            gpu_layers=gpu_layers,
            enable_vision=model.supports_vision and h.tier == "power",
            enable_complex_agents=h.tier in {"standard", "power"},
            concurrency=concurrency,
        )
