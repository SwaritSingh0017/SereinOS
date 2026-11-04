"""
Unit and Integration Test Suite for Milestone 1: Offline Local Speech Engine.
Verifies:
1. Module Unification: serein_core.voice_engine cleanly re-exports serein-voice.
2. Local Viseme Extraction: PiperTTSBackend extracts genuine timestamped VRM visemes (<800ms).
3. VisemeMapper: All 15 blend shapes, Azure, Oculus, and ARPAbet mappings.
4. Web API Endpoints: POST /api/voice/synthesize and POST /api/voice/transcribe.
5. End-to-end Voice Pipeline: Synthesis -> base64 audio -> transcription round-trip.
"""
from __future__ import annotations

import base64
import io
import time
import wave
from pathlib import Path
from typing import Dict, List

import numpy as np
import pytest
from fastapi.testclient import TestClient

# Ensure imports from unified serein_core.voice_engine
from serein_core.voice_engine import (
    AzureTTSBackend,
    HeadTTSBackend,
    MockSTTBackend,
    PiperTTS,
    PiperTTSBackend,
    STTBackend,
    STTRequest,
    STTResponse,
    SystemTTS,
    SystemTTSBackend,
    TTSBackend,
    TTSRequest,
    TTSResponse,
    VisemeMapper,
    VoiceConfig,
    VoiceEngine,
    VoiceProvider,
    WhisperSTT,
    WhisperSTTBackend,
    extract_visemes_from_audio_energy,
    get_voice_engine,
    voice_engine,
)
from serein_core.web_app import app


# ============================================================================
# Helper Functions: Synthetic Audio Generation
# ============================================================================

def make_synthetic_wav(
    duration_s: float = 1.0,
    sample_rate: int = 22050,
    freq: float = 330.0,
    amplitude: float = 0.5,
) -> bytes:
    """Generate a valid in-memory PCM 16-bit mono WAV buffer."""
    num_samples = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, num_samples, endpoint=False)
    # Dual-tone formant modulation with envelope
    envelope = np.sin(np.pi * t / duration_s) ** 2
    audio = amplitude * np.sin(2 * np.pi * freq * t) * envelope
    pcm_data = (audio * 32767).astype(np.int16).tobytes()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


# ============================================================================
# 1. Module Unification Tests
# ============================================================================

class TestModuleUnification:
    """Verify serein_core.voice_engine correctly re-exports serein-voice."""

    def test_reexports_presence_and_identity(self):
        """All expected classes and singletons must be present in serein_core.voice_engine."""
        assert VoiceEngine is not None
        assert VoiceProvider is not None
        assert TTSRequest is not None
        assert TTSResponse is not None
        assert STTRequest is not None
        assert STTResponse is not None
        assert PiperTTSBackend is not None
        assert WhisperSTTBackend is not None
        assert MockSTTBackend is not None
        assert SystemTTSBackend is not None
        assert VisemeMapper is not None
        assert voice_engine is not None
        assert get_voice_engine() is voice_engine

    def test_legacy_aliases_compatibility(self):
        """Legacy class names must alias to current backend implementations."""
        assert PiperTTS is PiperTTSBackend
        assert WhisperSTT is WhisperSTTBackend
        assert SystemTTS is SystemTTSBackend

    def test_voice_config_reexport(self):
        """VoiceConfig must be available and have expected default attributes."""
        cfg = VoiceConfig()
        assert hasattr(cfg, "enabled")
        assert hasattr(cfg, "wake_phrase")
        assert hasattr(cfg, "vad_energy_threshold")


# ============================================================================
# 2. VisemeMapper Unit Tests
# ============================================================================

class TestVisemeMapper:
    """Verify VisemeMapper blend shapes, Azure, Oculus, and Phoneme mappings."""

    ALL_15_SHAPES = [
        "aa", "ih", "e", "oh", "ou", "pp", "ff", "th",
        "dd", "kk", "ch", "ss", "nn", "rr", "sil"
    ]

    def test_all_15_blend_shapes_defined(self):
        """Check all 15 standard VRM blend shapes exist in ALL_BLEND_SHAPES."""
        assert set(VisemeMapper.ALL_BLEND_SHAPES) == set(self.ALL_15_SHAPES)

    def test_map_oculus_visemes(self):
        """MapOculusViseme must properly map all 15 standard shapes."""
        for shape in self.ALL_15_SHAPES:
            weights = VisemeMapper.MapOculusViseme(shape, intensity=1.0)
            assert weights[shape] == 1.0
            assert sum(v for k, v in weights.items() if k != shape) == 0.0

    def test_map_oculus_case_and_unknown(self):
        """Oculus mapping must handle uppercase and fallback unknown shapes to zeros."""
        w_upper = VisemeMapper.MapOculusViseme("AA", intensity=0.8)
        assert w_upper["aa"] == pytest.approx(0.8)

        w_unknown = VisemeMapper.MapOculusViseme("nonexistent_shape")
        assert all(v == 0.0 for v in w_unknown.values())

    def test_map_azure_visemes_valid_and_out_of_bounds(self):
        """MapAzureViseme must map 0..21 and fallback out-of-bounds to sil."""
        # 0 -> sil
        assert VisemeMapper.MapAzureViseme(0)["sil"] == 1.0
        # 10 -> aa
        assert VisemeMapper.MapAzureViseme(10)["aa"] == 1.0
        # 21 -> ch/ss
        w21 = VisemeMapper.MapAzureViseme(21)
        assert w21["ch"] == pytest.approx(0.9)
        assert w21["ss"] == pytest.approx(0.2)
        # Out-of-bounds
        assert VisemeMapper.MapAzureViseme(999)["sil"] == 1.0
        assert VisemeMapper.MapAzureViseme(-1)["sil"] == 1.0

    def test_map_phonemes(self):
        """MapPhoneme must map vowels, consonants, and unknown phonemes."""
        assert VisemeMapper.MapPhoneme("AA")["aa"] == 1.0
        assert VisemeMapper.MapPhoneme("P")["pp"] == 1.0
        assert VisemeMapper.MapPhoneme("S")["ss"] == 1.0
        assert VisemeMapper.MapPhoneme("UNKNOWN_PHONEME")["sil"] == 1.0


# ============================================================================
# 3. Local Viseme Extraction & Latency Tests
# ============================================================================

class TestVisemeExtraction:
    """Verify genuine timestamped viseme extraction from audio energy envelope."""

    def test_extract_visemes_from_valid_audio(self):
        """Extraction must produce valid timestamped viseme intervals."""
        wav_bytes = make_synthetic_wav(duration_s=1.2, freq=440.0)
        visemes = extract_visemes_from_audio_energy(
            wav_bytes, sample_rate=22050, text="Hello Serein"
        )

        assert len(visemes) > 0
        for v in visemes:
            assert "viseme" in v
            assert "time_ms" in v
            assert "duration_ms" in v
            assert "blend_shapes" in v
            assert v["viseme"] in VisemeMapper.ALL_BLEND_SHAPES
            assert v["duration_ms"] >= 30
            assert set(v["blend_shapes"].keys()) == set(VisemeMapper.ALL_BLEND_SHAPES)

        # Monotonic time check
        for i in range(len(visemes) - 1):
            assert visemes[i]["time_ms"] <= visemes[i + 1]["time_ms"]

    def test_viseme_extraction_latency_under_50ms(self):
        """Viseme extraction algorithm must be fast (< 50ms) to ensure TTS latency < 800ms."""
        wav_bytes = make_synthetic_wav(duration_s=2.5, freq=280.0)
        start = time.perf_counter()
        visemes = extract_visemes_from_audio_energy(
            wav_bytes, sample_rate=22050, text="SereinOS neural operating system companion"
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50.0, f"Extraction took {elapsed_ms}ms, expected < 50ms"
        assert len(visemes) > 0

    def test_piper_tts_backend_extract_visemes_method(self):
        """PiperTTSBackend must provide extract_visemes method and static helper."""
        backend = PiperTTSBackend(binary_path="/usr/bin/piper")
        wav_bytes = make_synthetic_wav(duration_s=0.8, freq=350.0)

        res1 = backend.extract_visemes(wav_bytes, 22050, "testing piper visemes")
        res2 = PiperTTSBackend.extract_visemes_static(wav_bytes, 22050, "testing piper visemes")

        assert len(res1) > 0
        assert len(res2) > 0
        assert res1[0]["viseme"] == res2[0]["viseme"]

    def test_extract_visemes_empty_and_silence_audio(self):
        """Empty audio or pure silence must return 'sil' viseme cleanly."""
        v_empty = extract_visemes_from_audio_energy(b"")
        assert len(v_empty) == 1
        assert v_empty[0]["viseme"] == "sil"

        # Silence WAV (all zeros)
        silence_pcm = (np.zeros(22050, dtype=np.int16)).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(silence_pcm)

        v_sil = extract_visemes_from_audio_energy(buf.getvalue(), 22050, "")
        assert all(v["viseme"] == "sil" for v in v_sil)


# ============================================================================
# 4. Web API Endpoints Tests (/api/voice/synthesize & /api/voice/transcribe)
# ============================================================================

class TestVoiceWebAPI:
    """Verify REST API endpoints for speech synthesis and transcription."""

    @pytest.fixture(scope="class")
    def client(self):
        """FastAPI TestClient instance."""
        return TestClient(app)

    def test_post_voice_synthesize_success(self, client):
        """POST /api/voice/synthesize returns WAV audio base64, duration, latency, and visemes."""
        payload = {"text": "Hello Serein"}
        response = client.post("/api/voice/synthesize", json=payload)

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert data["format"] == "wav"
        assert "audio_base64" in data
        assert len(data["audio_base64"]) > 0
        assert "visemes" in data
        assert isinstance(data["visemes"], list)
        assert len(data["visemes"]) > 0
        assert "duration_ms" in data
        assert data["duration_ms"] > 0
        assert "latency_ms" in data
        assert isinstance(data["latency_ms"], int)

        # Validate that audio_base64 is decodable valid WAV
        raw_audio = base64.b64decode(data["audio_base64"])
        assert raw_audio.startswith(b"RIFF")
        with wave.open(io.BytesIO(raw_audio), "rb") as wf:
            assert wf.getnchannels() in (1, 2)
            assert wf.getsampwidth() == 2
            assert wf.getframerate() in (16000, 22050, 24000, 44100, 48000)

        # Validate visemes structure
        first_viseme = data["visemes"][0]
        assert "viseme" in first_viseme
        assert "time_ms" in first_viseme
        assert "duration_ms" in first_viseme
        assert first_viseme["viseme"] in VisemeMapper.ALL_BLEND_SHAPES

    def test_post_voice_synthesize_empty_text_error(self, client):
        """POST /api/voice/synthesize with empty text must return HTTP 400."""
        response = client.post("/api/voice/synthesize", json={"text": "   "})
        assert response.status_code == 400

    def test_post_voice_transcribe_with_json_base64(self, client):
        """POST /api/voice/transcribe with JSON audio_base64 returns transcript."""
        wav_bytes = make_synthetic_wav(duration_s=1.0)
        b64_audio = base64.b64encode(wav_bytes).decode("ascii")

        response = client.post("/api/voice/transcribe", json={"audio_base64": b64_audio})
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert "text" in data
        assert isinstance(data["text"], str)
        assert "confidence" in data
        assert "duration_ms" in data

    def test_post_voice_transcribe_with_multipart_file(self, client):
        """POST /api/voice/transcribe with multipart file upload returns transcript."""
        wav_bytes = make_synthetic_wav(duration_s=1.0)
        files = {"file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")}

        response = client.post("/api/voice/transcribe", files=files)
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert "text" in data

    def test_post_voice_transcribe_missing_audio_error(self, client):
        """POST /api/voice/transcribe with missing audio must return HTTP 400."""
        response = client.post("/api/voice/transcribe", json={})
        assert response.status_code == 400


# ============================================================================
# 5. Full End-to-End Voice Round-Trip Verification
# ============================================================================

class TestVoiceRoundTrip:
    """Verify the complete synthesis -> playback audio -> transcription loop."""

    def test_synthesize_and_transcribe_roundtrip(self):
        """Synthesize text via web API and transcribe the generated audio."""
        client = TestClient(app)
        query = "Launch the terminal"

        # 1. Synthesize
        syn_resp = client.post("/api/voice/synthesize", json={"text": query})
        assert syn_resp.status_code == 200
        syn_data = syn_resp.json()

        audio_b64 = syn_data["audio_base64"]
        visemes = syn_data["visemes"]
        assert len(audio_b64) > 100
        assert len(visemes) > 0

        # 2. Transcribe
        tr_resp = client.post("/api/voice/transcribe", json={"audio_base64": audio_b64})
        assert tr_resp.status_code == 200
        tr_data = tr_resp.json()

        assert tr_data["status"] == "ok"
        assert len(tr_data["text"]) > 0
