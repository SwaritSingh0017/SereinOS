"""
E2E Test Configuration and Shared Fixtures for SereinOS.
Provides isolated environments, mocked hardware/network stubs,
and common test helpers for all 4 E2E testing tiers.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Generator, AsyncGenerator, Dict, Any, List
import pytest
from starlette.testclient import TestClient

# Ensure serein-core and serein-voice are importable
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORE_SRC = REPO_ROOT / "serein-core" / "src"
VOICE_SRC = REPO_ROOT / "serein-voice"

for p in (str(CORE_SRC), str(VOICE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import serein_core.config as config_module
from serein_core.config import CoreConfig, VoiceConfig, DatabaseConfig, get_config
from serein_core.events import EventBus, CoreEvents, event_bus
from serein_core.database import Database
from serein_core.intent.classifier import IntentClassifier
from serein_core.decision import DecisionEngine
from serein_core.hardware import HardwareDetector, HardwareProfile
from serein_core.model_manager import LocalModelManager
from serein_core.context import ContextEngine
from serein_core.ai import BrainRouter, ModelTier
from serein_core.web_app import app

# Voice modules
from audio_capture import (
    AudioChunk,
    HeadlessAudioSource,
    AudioCaptureLoop,
    generate_silence_pcm,
    generate_sine_pcm,
)
from vad import VoiceActivityDetector, UtteranceSegmenter, VADState
from wake_word import AcousticPatternDetector, WakeWordDetector, generate_wake_word_pcm
from voice_engine import VisemeMapper, MockSTTBackend, VoiceEngine, TTSRequest, STTRequest


@pytest.fixture(autouse=True)
def clean_global_state():
    """Ensure clean event bus and config state between tests."""
    from collections import defaultdict
    event_bus._subscribers = defaultdict(list)
    event_bus._typed_subscribers = defaultdict(list)
    event_bus._history = []
    yield
    event_bus._subscribers = defaultdict(list)
    event_bus._typed_subscribers = defaultdict(list)
    event_bus._history = []


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a dedicated clean temporary directory."""
    td = tempfile.mkdtemp(prefix="serein_e2e_")
    path = Path(td)
    yield path
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
def mock_db_path(temp_dir: Path) -> Path:
    """Return path for an isolated test SQLite database."""
    return temp_dir / "serein_test.db"


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """FastAPI TestClient for web dashboard verification."""
    with TestClient(app) as client:
        yield client


@pytest.fixture
def vaio_hardware_profile() -> HardwareProfile:
    """Standardized HardwareProfile for Sony VAIO VPCYB35AN (AMD E-450 APU)."""
    return HardwareProfile(
        os_name="SereinOS Linux",
        architecture="x86_64",
        cpu_name="AMD E-450 APU with Radeon(tm) HD Graphics",
        cpu_cores=2,
        cpu_threads=2,
        ram_gb=4.0,
        gpu_name="AMD Radeon HD 6320",
        gpu_vram_gb=0.5,
        storage_free_gb=120.0,
        has_avx=False,
        has_avx2=False,
        has_cuda=False,
        has_rocm=False,
        tier="minimum",
    )


@pytest.fixture
def sample_audio_chunk() -> AudioChunk:
    """100ms of 16kHz 16-bit mono 440Hz sine wave."""
    pcm = generate_sine_pcm(duration_s=0.1, freq_hz=440.0, amplitude=0.5, sample_rate=16000)
    return AudioChunk(data=pcm, sample_rate=16000, channels=1, sample_width=2)


@pytest.fixture
def silence_audio_chunk() -> AudioChunk:
    """100ms of silence."""
    pcm = generate_silence_pcm(duration_s=0.1, sample_rate=16000)
    return AudioChunk(data=pcm, sample_rate=16000, channels=1, sample_width=2)
