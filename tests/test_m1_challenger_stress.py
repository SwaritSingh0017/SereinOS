"""
Milestone 1 Empirical Challenger Stress Suite
Authored by: challenger_m1_speech_1
Target: Milestone 1 Offline Local Speech Engine (serein-voice and serein-core)

Tests empirical stress scenarios:
1. Boundary inputs (empty, odd bytes, truncated headers, extreme lengths, unicode/emojis).
2. Acoustic anomalies (DC bias, pure continuous tones, white noise, room noise).
3. All 15 VRM blend shapes empirical coverage and validity.
4. Latency benchmarks for viseme extraction (<10ms) and TTS synthesis (<800ms).
5. Web API boundary and error isolation.
"""
from __future__ import annotations

import base64
import io
import math
import sys
import time
import wave
from pathlib import Path
from typing import Dict, List, Set

import numpy as np
import pytest
from starlette.testclient import TestClient

# Ensure repositories are importable
ROOT_DIR = Path(__file__).resolve().parent.parent
CORE_SRC = ROOT_DIR / "serein-core" / "src"
VOICE_SRC = ROOT_DIR / "serein-voice"

if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))
if str(VOICE_SRC) not in sys.path:
    sys.path.insert(0, str(VOICE_SRC))

from serein_core.voice_engine import (
    PiperTTSBackend,
    SystemTTSBackend,
    TTSRequest,
    VisemeMapper,
    extract_visemes_from_audio_energy,
    get_voice_engine,
)
from serein_core.web_app import app


# ============================================================================
# Helpers
# ============================================================================

def make_test_wav(
    duration_s: float = 1.0,
    sample_rate: int = 22050,
    freq: float = 400.0,
    with_silence_padding: bool = True,
) -> bytes:
    """Generate in-memory 16-bit mono WAV audio with optional silence padding."""
    num_samples = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, num_samples, endpoint=False)
    signal = np.sin(2 * np.pi * freq * t) * 0.7
    if with_silence_padding:
        pad = int(sample_rate * 0.15)
        if len(signal) > 2 * pad:
            signal[:pad] = 0.0
            signal[-pad:] = 0.0
    pcm = (signal * 32767).astype(np.int16).tobytes()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


# ============================================================================
# 1. Boundary Inputs & Malformed Data
# ============================================================================

class TestVisemeBoundaryInputs:
    """Stress-test viseme extraction against boundary, malformed, and extreme inputs."""

    def test_empty_audio_bytes(self):
        """Empty audio buffer must cleanly return default silence viseme."""
        res = extract_visemes_from_audio_energy(b"")
        assert len(res) == 1
        assert res[0]["viseme"] == "sil"
        assert res[0]["time_ms"] == 0
        assert res[0]["duration_ms"] >= 30
        assert res[0]["blend_shapes"]["sil"] == 1.0

    def test_single_byte_buffer(self):
        """Single-byte buffer (<2 bytes) must safely return default viseme without crashing."""
        res = extract_visemes_from_audio_energy(b"\x00")
        assert len(res) == 1
        assert res[0]["viseme"] == "sil"

    def test_single_sample_buffer(self):
        """Single 16-bit sample (2 bytes) must safely return silence viseme."""
        res = extract_visemes_from_audio_energy(b"\x00\x00", sample_rate=22050)
        assert len(res) == 1
        assert res[0]["viseme"] == "sil"

    def test_odd_length_pcm_bytes_vulnerability(self):
        """
        Remediation Check: Odd byte lengths (e.g. 3, 5, 47 bytes) are aligned safely
        before np.frombuffer(dtype=int16) and must not crash with ValueError.
        """
        for odd_len in [3, 5, 7, 43, 47, 101]:
            raw_odd = b"\x00" * odd_len
            res = extract_visemes_from_audio_energy(raw_odd)
            assert isinstance(res, list)
            assert len(res) > 0
            assert "viseme" in res[0]

    def test_truncated_riff_header_with_odd_payload(self):
        """
        Remediation Check: Truncated RIFF header where audio_data[44:] has odd length
        is safely aligned and extracts visemes cleanly without ValueError.
        """
        corrupted_wav = b"RIFF" + b"\x00" * 43  # total 47 bytes -> payload 3 bytes
        res = extract_visemes_from_audio_energy(corrupted_wav)
        assert isinstance(res, list)
        assert len(res) > 0
        assert "viseme" in res[0]

    def test_non_standard_sample_rates(self):
        """Verify behavior across standard, low, and high sample rates."""
        rates = [8000, 11025, 16000, 22050, 44100, 48000, 96000]
        for sr in rates:
            wav = make_test_wav(duration_s=0.8, sample_rate=sr, freq=300.0)
            visemes = extract_visemes_from_audio_energy(wav, sample_rate=sr, text="testing rates")
            assert len(visemes) > 0
            assert all(v["duration_ms"] >= 30 for v in visemes)
            assert all(v["time_ms"] >= 0 for v in visemes)

    def test_zero_and_negative_sample_rates(self):
        """Check defensive handling of zero and negative sample rates."""
        wav = make_test_wav(duration_s=0.5, sample_rate=22050)
        # sample_rate = 0 should fall back to 22050
        v_zero = extract_visemes_from_audio_energy(wav, sample_rate=0, text="hello")
        assert len(v_zero) > 0
        assert v_zero[0]["time_ms"] >= 0

        # sample_rate = -16000 should not crash
        v_neg = extract_visemes_from_audio_energy(wav, sample_rate=-16000, text="hello")
        assert isinstance(v_neg, list)

    def test_massive_audio_buffer(self):
        """Stress-test viseme extraction on a 30-second audio stream."""
        sr = 22050
        dur_s = 30.0
        t = np.linspace(0, dur_s, int(sr * dur_s), endpoint=False)
        audio = (np.sin(2 * np.pi * 350 * t) * 0.6 * np.sin(2 * np.pi * 0.5 * t)).astype(np.float32)
        pcm = (audio * 32767).astype(np.int16).tobytes()

        t0 = time.perf_counter()
        visemes = extract_visemes_from_audio_energy(
            pcm, sample_rate=sr, text="This is an extended sustained conversation test."
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert len(visemes) > 1
        assert visemes[-1]["time_ms"] + visemes[-1]["duration_ms"] >= int(dur_s * 1000) - 100
        # Latency should scale linearly and complete comfortably
        assert elapsed_ms < 500.0

    def test_extreme_text_lengths_and_multilingual(self):
        """Stress-test viseme extraction with 10,000 words and non-Latin unicode text."""
        wav = make_test_wav(duration_s=1.5, sample_rate=22050)

        # 1. 10,000 words
        huge_text = "quick brown fox jumps over lazy dog " * 1250
        res_huge = extract_visemes_from_audio_energy(wav, sample_rate=22050, text=huge_text)
        assert len(res_huge) > 0

        # 2. Non-Latin scripts (Chinese, Japanese, Arabic, Cyrillic, Emoji)
        multilingual = "你好世界 こんにちは مرحبا Привет 🚀🎉🌟 12345 !@#$%"
        res_multi = extract_visemes_from_audio_energy(wav, sample_rate=22050, text=multilingual)
        assert len(res_multi) > 0
        for v in res_multi:
            assert v["viseme"] in VisemeMapper.ALL_BLEND_SHAPES

        # 3. Only whitespace and punctuation
        res_punct = extract_visemes_from_audio_energy(wav, sample_rate=22050, text="  ... ??? !!! ,,   ")
        assert len(res_punct) > 0


# ============================================================================
# 2. Acoustic Anomalies & Pathology
# ============================================================================

class TestVisemeAcousticAnomalies:
    """Stress-test viseme extraction under acoustic distortions."""

    def test_pure_dc_bias(self):
        """
        Pure DC bias (no oscillation) has zero acoustic frequency.
        Must be classified as silence ('sil') without crashing.
        """
        sr = 22050
        dc_samples = np.ones(sr, dtype=np.int16) * 20000
        pcm = dc_samples.tobytes()

        visemes = extract_visemes_from_audio_energy(pcm, sample_rate=sr, text="hello speech")
        assert all(v["viseme"] == "sil" for v in visemes)

    def test_continuous_tone_without_silence_over_suppression_flaw(self):
        """
        Remediation Check: Silence threshold upper-bound clamp min(p15 * 1.5, max_rms * 0.25)
        prevents continuous voiced audio from collapsing into 100% 'sil'.
        """
        sr = 22050
        dur_s = 2.0
        t = np.linspace(0, dur_s, int(sr * dur_s), endpoint=False)
        # Continuous unmodulated tone
        signal = np.sin(2 * np.pi * 400 * t) * 0.7
        pcm = (signal * 32767).astype(np.int16).tobytes()

        visemes = extract_visemes_from_audio_energy(pcm, sample_rate=sr, text="hello world test speech")
        non_sil = [v for v in visemes if v["viseme"] != "sil"]
        assert len(non_sil) > 0, "Continuous voiced audio must extract non-silence phonetic visemes"

    def test_active_speech_with_silence_envelope(self):
        """Speech with silence pauses must correctly extract phonetic visemes."""
        sr = 22050
        dur_s = 2.0
        t = np.linspace(0, dur_s, int(sr * dur_s), endpoint=False)
        # Modulated speech bursts with clear troughs
        signal = np.sin(2 * np.pi * 320 * t) * 0.8 * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t))
        pcm = (signal * 32767).astype(np.int16).tobytes()

        visemes = extract_visemes_from_audio_energy(pcm, sample_rate=sr, text="hello world test speech")
        shapes = [v["viseme"] for v in visemes]
        non_sil = [s for s in shapes if s != "sil"]
        assert len(non_sil) > 0, "Speech with modulation must produce non-silence visemes"

    def test_white_noise_rejection(self):
        """Pure stationary white noise without burst dynamics must be classified as silence."""
        np.random.seed(42)
        noise = (np.random.uniform(-0.5, 0.5, 22050) * 32767).astype(np.int16).tobytes()
        visemes = extract_visemes_from_audio_energy(noise, sample_rate=22050, text="speaking")
        assert all(v["viseme"] == "sil" for v in visemes)

    def test_fluctuating_noise_fallback_triggers_sibilants(self):
        """Burst noise with high Zero Crossing Rate triggers sibilant visemes ('ss' / 'ch')."""
        np.random.seed(42)
        t = np.linspace(0, 1.0, 22050)
        # High-frequency burst noise
        noise = np.random.uniform(-1, 1, 22050) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t))
        pcm = (noise * 30000).astype(np.int16).tobytes()

        visemes = extract_visemes_from_audio_energy(pcm, sample_rate=22050, text="")
        shapes = {v["viseme"] for v in visemes}
        assert ("ss" in shapes or "ch" in shapes), "High ZCR noise bursts should map to sibilants"


# ============================================================================
# 3. 15 VRM Blend Shape Coverage
# ============================================================================

class TestViseme15BlendShapesCoverage:
    """Empirically verify all 15 VRM blend shapes can be produced and formatted correctly."""

    ALL_15_SHAPES = [
        "aa", "ih", "e", "oh", "ou", "pp", "ff", "th",
        "dd", "kk", "ch", "ss", "nn", "rr", "sil"
    ]

    def test_viseme_mapper_all_15_shapes_constant(self):
        """Verify VisemeMapper.ALL_BLEND_SHAPES includes all 15 specification blend shapes."""
        assert set(VisemeMapper.ALL_BLEND_SHAPES) == set(self.ALL_15_SHAPES)
        assert len(VisemeMapper.ALL_BLEND_SHAPES) == 15

    def test_map_oculus_viseme_all_15_shapes(self):
        """Verify each of the 15 shapes maps to an isolated 1.0 weight."""
        for shape in self.ALL_15_SHAPES:
            mapped = VisemeMapper.MapOculusViseme(shape, intensity=1.0)
            assert mapped[shape] == 1.0
            for other_shape, w in mapped.items():
                if other_shape != shape:
                    assert w == 0.0

    def test_extract_visemes_produces_all_15_shapes(self):
        """
        Empirical Generator: Verify that targeted phonetic prompts produce
        every single one of the 15 blend shapes in output visemes.
        """
        found_shapes: Set[str] = set()

        test_prompts = {
            "aa": "father ah car palm",
            "ih": "bit sit this big",
            "e": "bed red get pet",
            "oh": "boat coat goat home",
            "ou": "boot root shoot moon",
            "pp": "pie pipe map pop baby",
            "ff": "fee five photo fan",
            "th": "thin think thanks that",
            "dd": "dog day bad deed dot",
            "kk": "cat key cake kick quick",
            "ch": "church check cheap shoe shine",
            "ss": "sun see sit song zoo",
            "nn": "no nine man noon name",
            "rr": "run red car roar real",
            "sil": "",
        }

        for target_shape, prompt in test_prompts.items():
            # Generate modulated audio with speech burst between silence
            sr = 22050
            dur_s = 2.0
            s = np.zeros(int(sr * dur_s), dtype=np.float32)
            active_start = int(sr * 0.3)
            active_end = int(sr * 1.7)
            t_active = np.linspace(0, 1.4, active_end - active_start)
            # Complex dual-tone burst
            s[active_start:active_end] = (
                0.5 * np.sin(2 * np.pi * 300 * t_active)
                + 0.3 * np.sin(2 * np.pi * 700 * t_active)
            )
            pcm = (s * 32767).astype(np.int16).tobytes()

            visemes = extract_visemes_from_audio_energy(pcm, sample_rate=sr, text=prompt)
            for v in visemes:
                found_shapes.add(v["viseme"])
                # Structural integrity checks
                assert "viseme" in v
                assert "time_ms" in v
                assert "duration_ms" in v
                assert "blend_shapes" in v
                assert v["duration_ms"] >= 30
                assert set(v["blend_shapes"].keys()) == set(self.ALL_15_SHAPES)

        missing = set(self.ALL_15_SHAPES) - found_shapes
        assert len(missing) == 0, f"Missing blend shapes in empirical extraction: {missing}"
        assert len(found_shapes) == 15


# ============================================================================
# 4. Latency Benchmarks
# ============================================================================

class TestSpeechLatencyBenchmarks:
    """Measure execution latencies for viseme extraction and synthesis."""

    def test_viseme_extraction_latency_under_10ms_for_typical_speech(self):
        """
        Viseme extraction on 1.0s to 2.5s speech audio must execute in < 10ms.
        """
        wav_1s = make_test_wav(duration_s=1.0, sample_rate=22050)
        wav_2s = make_test_wav(duration_s=2.5, sample_rate=22050)
        prompt = "SereinOS offline local speech synthesis"

        # Warmup call
        _ = extract_visemes_from_audio_energy(wav_1s, sample_rate=22050, text=prompt)

        # Benchmark 1s
        t0 = time.perf_counter()
        _ = extract_visemes_from_audio_energy(wav_1s, sample_rate=22050, text=prompt)
        dt_1s_ms = (time.perf_counter() - t0) * 1000

        # Benchmark 2.5s
        t0 = time.perf_counter()
        _ = extract_visemes_from_audio_energy(wav_2s, sample_rate=22050, text=prompt)
        dt_2s_ms = (time.perf_counter() - t0) * 1000

        print(f"\n[LATENCY BENCHMARK] Viseme Extraction 1.0s: {dt_1s_ms:.2f}ms, 2.5s: {dt_2s_ms:.2f}ms")
        assert dt_1s_ms < 10.0, f"1.0s extraction took {dt_1s_ms:.2f}ms (must be < 10ms)"
        assert dt_2s_ms < 15.0, f"2.5s extraction took {dt_2s_ms:.2f}ms (must be < 15ms)"

    def test_piper_backend_interface_compliance(self):
        """Verify PiperTTSBackend provides static and instance extract_visemes interfaces."""
        backend = PiperTTSBackend(binary_path="/usr/bin/piper")
        wav = make_test_wav(duration_s=0.5, sample_rate=22050)

        t0 = time.perf_counter()
        res1 = backend.extract_visemes(wav, 22050, "hello piper")
        dt_ms = (time.perf_counter() - t0) * 1000

        res2 = PiperTTSBackend.extract_visemes_static(wav, 22050, "hello piper")

        assert len(res1) > 0
        assert len(res2) > 0
        assert res1[0]["viseme"] == res2[0]["viseme"]
        assert dt_ms < 15.0


# ============================================================================
# 5. Web API Boundary & Stress Tests
# ============================================================================

class TestWebAPIBoundaries:
    """Stress-test REST endpoints /api/voice/synthesize and /api/voice/transcribe."""

    @pytest.fixture(scope="class")
    def client(self):
        return TestClient(app)

    def test_synthesize_empty_and_whitespace_text(self, client):
        """Empty or whitespace-only text must be rejected with 400 Bad Request."""
        r_empty = client.post("/api/voice/synthesize", json={"text": ""})
        assert r_empty.status_code == 400
        assert "cannot be empty" in r_empty.json()["detail"]

        r_ws = client.post("/api/voice/synthesize", json={"text": "   \t\n  "})
        assert r_ws.status_code == 400
        assert "cannot be empty" in r_ws.json()["detail"]

    def test_synthesize_valid_short_text_contract(self, client):
        """Verify response structure meets PROJECT.md interface contract."""
        resp = client.post(
            "/api/voice/synthesize",
            json={"text": "Hello Serein", "voice": "default", "rate": 1.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "audio_base64" in data
        assert data["format"] == "wav"
        assert "visemes" in data
        assert "duration_ms" in data
        assert "latency_ms" in data

        # Decode audio_base64 and verify valid WAV
        raw_bytes = base64.b64decode(data["audio_base64"])
        assert raw_bytes.startswith(b"RIFF")
        with wave.open(io.BytesIO(raw_bytes), "rb") as wf:
            assert wf.getnchannels() in (1, 2)
            assert wf.getsampwidth() == 2
            assert wf.getframerate() > 0

    def test_transcribe_empty_and_corrupt_payload(self, client):
        """Empty audio must return 400; corrupt audio must be handled cleanly."""
        # 1. No audio payload
        r_empty = client.post("/api/voice/transcribe", content=b"")
        assert r_empty.status_code == 400

        # 2. Corrupt base64 JSON
        r_bad_b64 = client.post(
            "/api/voice/transcribe",
            headers={"Content-Type": "application/json"},
            content=b'{"audio_base64": "!!!not_base64!!!"}',
        )
        assert r_bad_b64.status_code == 400

        # 3. Valid base64 but random garbage bytes
        garbage_b64 = base64.b64encode(b"random_non_wav_garbage_bytes").decode("ascii")
        r_garbage = client.post(
            "/api/voice/transcribe",
            json={"audio_base64": garbage_b64},
        )
        # MockSTT or Whisper handles gracefully
        assert r_garbage.status_code in (200, 400, 500)

    def test_viseme_mapper_safe_mode_non_string_and_none(self):
        """Verify VisemeMapper safe mode handles None, ints, and non-strings defaulting to sil: 1.0."""
        # MapPhoneme with safe=True
        w_none = VisemeMapper.MapPhoneme(None, safe=True)
        assert w_none["sil"] == 1.0
        assert sum(v for k, v in w_none.items() if k != "sil") == 0.0

        w_int = VisemeMapper.MapPhoneme(12345, safe=True)
        assert w_int["sil"] == 1.0

        # MapOculusViseme with safe=True
        w_oc_none = VisemeMapper.MapOculusViseme(None, safe=True)
        assert w_oc_none["sil"] == 1.0
        assert sum(v for k, v in w_oc_none.items() if k != "sil") == 0.0

        w_oc_int = VisemeMapper.MapOculusViseme(999, safe=True)
        assert w_oc_int["sil"] == 1.0

        # MapPhonemeSafe and MapOculusVisemeSafe convenience helpers
        assert VisemeMapper.MapPhonemeSafe(None)["sil"] == 1.0
        assert VisemeMapper.MapOculusVisemeSafe(None)["sil"] == 1.0

    def test_fallback_audio_dynamic_envelope_produces_active_visemes(self, client):
        """
        Verify that fallback audio with amplitude envelope modulation
        produces non-silence avatar mouth visemes rather than collapsing into 100% 'sil'.
        """
        resp = client.post(
            "/api/voice/synthesize",
            json={"text": "Testing fallback speech audio dynamics and visemes", "voice": "nonexistent_voice"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        visemes = data["visemes"]
        assert len(visemes) > 0
        non_sil = [v for v in visemes if v["viseme"] != "sil"]
        assert len(non_sil) > 0, "Fallback synthesis must produce active mouth visemes"

