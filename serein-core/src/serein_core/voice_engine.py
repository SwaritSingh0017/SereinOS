"""
Voice Engine Module Unification - SereinOS.
Wraps and re-exports the standalone serein-voice subsystem for seamless
integration with SereinOS core daemon, web application, and event bus.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure serein-voice subsystem is in sys.path
_CANDIDATE_PATHS = [
    Path(__file__).resolve().parent.parent.parent.parent / "serein-voice",
    Path(__file__).resolve().parent.parent / "serein-voice",
    Path(__file__).resolve().parent / "serein-voice",
    Path("/usr/local/lib/serein/serein-voice"),
    Path("/usr/local/share/serein/voice"),
]
for p in _CANDIDATE_PATHS:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Core config VoiceConfig
from serein_core.config import VoiceConfig

# Re-export core voice engine types and backends
try:
    from voice_engine import (
        AzureTTSBackend,
        HeadTTSBackend,
        MockSTTBackend,
        PiperTTSBackend,
        STTBackend,
        STTRequest,
        STTResponse,
        SystemTTSBackend,
        TTSBackend,
        TTSRequest,
        TTSResponse,
        VisemeMapper,
        VoiceEngine,
        VoiceProvider,
        WhisperSTTBackend,
        extract_visemes_from_audio_energy,
    )
except ImportError:
    # Graceful fallbacks when standalone serein-voice is not loaded
    class TTSRequest:
        def __init__(self, text="", voice="default", pitch=1.0, rate=1.0):
            self.text = text
            self.voice = voice
            self.pitch = pitch
            self.rate = rate

    class TTSResponse:
        def __init__(self, audio_data=b"", sample_rate=22050):
            self.audio_data = audio_data
            self.sample_rate = sample_rate

    class STTRequest: pass
    class STTResponse: pass
    class TTSBackend: pass
    class STTBackend: pass
    class PiperTTSBackend: pass
    class WhisperSTTBackend: pass
    class MockSTTBackend: pass
    class SystemTTSBackend: pass
    class AzureTTSBackend: pass
    class HeadTTSBackend: pass
    class VoiceProvider: pass
    class VisemeMapper: pass
    def extract_visemes_from_audio_energy(*args, **kwargs): return []
    class VoiceEngine:
        def __init__(self, *args, **kwargs): pass
        async def synthesize(self, text, *args, **kwargs): return TTSResponse()
        async def transcribe(self, audio, *args, **kwargs): return ""

# Optional capture, VAD, and wake word re-exports
try:
    from audio_capture import (
        AudioCaptureLoop,
        AudioCaptureSource,
        AudioChunk,
        HardwareAudioCapture,
        HeadlessAudioSource,
        generate_silence_pcm,
        generate_sine_pcm,
    )
except ImportError:
    pass

try:
    from vad import UtteranceSegmenter, VADState, VoiceActivityDetector
except ImportError:
    pass

try:
    from wake_word import (
        AcousticPatternDetector,
        OpenWakeWordBackend,
        WakeWordBackend,
        WakeWordDetector,
        WakeWordResult,
        generate_wake_word_pcm,
    )
except ImportError:
    pass

# Backward compatibility aliases for legacy prototype classes
WhisperSTT = WhisperSTTBackend
FasterWhisperSTT = WhisperSTTBackend
VoskSTT = WhisperSTTBackend
PiperTTS = PiperTTSBackend
CoquiTTS = PiperTTSBackend
SystemTTS = SystemTTSBackend


def get_voice_engine() -> VoiceEngine:
    """Return the global unified VoiceEngine singleton."""
    import voice_engine as _ve
    return getattr(_ve, "voice_engine", None)


def __getattr__(name: str) -> Any:
    if name == "voice_engine":
        return get_voice_engine()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "VoiceConfig",
    "VoiceEngine",
    "VoiceProvider",
    "TTSRequest",
    "TTSResponse",
    "STTRequest",
    "STTResponse",
    "TTSBackend",
    "STTBackend",
    "PiperTTSBackend",
    "WhisperSTTBackend",
    "MockSTTBackend",
    "SystemTTSBackend",
    "AzureTTSBackend",
    "HeadTTSBackend",
    "VisemeMapper",
    "extract_visemes_from_audio_energy",
    "voice_engine",
    "get_voice_engine",
    "WhisperSTT",
    "FasterWhisperSTT",
    "VoskSTT",
    "PiperTTS",
    "CoquiTTS",
    "SystemTTS",
]