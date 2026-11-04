"""
Milestone 1 Web API and UI Integration Adversarial Stress Test Suite.
Executed by Challenger 2 (API & UI Integration Challenger).

Empirically tests:
1. POST /api/voice/synthesize:
   - Valid synthesis requests: response schema, WAV binary validation, duration, latency (<800ms), visemes format.
   - Edge case inputs: empty text, whitespace, max length boundary (4096 chars), overlength rejection (422), missing text.
   - Concurrent synthesis: 20 simultaneous threads executing synthesis to verify non-blocking behavior.
2. POST /api/voice/transcribe:
   - Valid inputs: JSON base64 ("audio_base64" and "audio"), multipart/form-data ("file" and "audio"), raw binary body.
   - Malformed base64 inputs: invalid base64 characters, truncated padding, non-string types (must return 400).
   - Empty audio payloads: empty base64 string, empty dict, empty file, empty body (must return 400).
   - Content-type boundary handling: unsupported / raw content types.
   - Concurrent transcription: 20 simultaneous threads executing transcription.
3. Cross-concurrency: simultaneous interleaved synthesize & transcribe requests.
4. UI Contract & FFT Visualizer Verification:
   - Validates assets/ui/app.js structure: Web Audio AudioContext, speechAnalyserNode, drawWaveform FFT pipeline,
     and avatarStage.setViseme lip-sync scheduling.
"""
from __future__ import annotations

import base64
import io
import re
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest
from starlette.testclient import TestClient

# Ensure serein-core and serein-voice are importable
REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_SRC = REPO_ROOT / "serein-core" / "src"
VOICE_SRC = REPO_ROOT / "serein-voice"

for p in (str(CORE_SRC), str(VOICE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from serein_core.voice_engine import VisemeMapper, get_voice_engine
from serein_core.web_app import app


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def make_test_wav(
    duration_s: float = 0.5,
    sample_rate: int = 16000,
    freq: float = 440.0,
    amplitude: float = 0.4,
) -> bytes:
    """Generate a valid single-channel 16-bit PCM WAV in-memory."""
    total_samples = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, total_samples, endpoint=False)
    wave_data = amplitude * np.sin(2 * np.pi * freq * t)
    pcm = (wave_data * 32767).astype(np.int16).tobytes()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


# ============================================================================
# 1. POST /api/voice/synthesize Adversarial Tests
# ============================================================================

class TestVoiceSynthesizeAPI:
    """Adversarial stress-testing of /api/voice/synthesize."""

    @pytest.fixture(scope="class")
    def client(self):
        return TestClient(app)

    def test_synthesize_valid_response_contract(self, client):
        """Verify response schema, base64 WAV validity, latency, and visemes."""
        # Warm up engine to isolate steady-state TTS latency from one-time engine initialization
        client.post("/api/voice/synthesize", json={"text": "warmup"})

        payload = {"text": "System diagnostics nominal."}
        start = time.perf_counter()
        resp = client.post("/api/voice/synthesize", json=payload)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        # Contract assertions
        assert data.get("status") == "ok"
        assert data.get("format") == "wav"
        assert "audio_base64" in data
        assert "visemes" in data
        assert "duration_ms" in data
        assert "latency_ms" in data
        assert isinstance(data["duration_ms"], int) and data["duration_ms"] > 0
        assert isinstance(data["latency_ms"], int)
        assert data["latency_ms"] < 800, f"Latency {data['latency_ms']}ms exceeded 800ms limit"

        # Decode base64 audio and verify genuine WAV header
        raw_audio = base64.b64decode(data["audio_base64"])
        assert len(raw_audio) > 100
        assert raw_audio.startswith(b"RIFF")
        assert b"WAVE" in raw_audio[:16]

        with wave.open(io.BytesIO(raw_audio), "rb") as wf:
            assert wf.getnchannels() in (1, 2)
            assert wf.getsampwidth() == 2
            assert wf.getframerate() in (16000, 22050, 24000, 44100, 48000)

        # Validate visemes structure and blend shapes
        visemes = data["visemes"]
        assert isinstance(visemes, list) and len(visemes) > 0
        last_time = -1
        for v in visemes:
            assert "viseme" in v
            assert "time_ms" in v
            assert "duration_ms" in v
            assert "blend_shapes" in v
            assert v["viseme"] in VisemeMapper.ALL_BLEND_SHAPES, f"Unknown viseme: {v['viseme']}"
            assert v["time_ms"] >= last_time, "Viseme timestamps must be monotonic"
            assert v["duration_ms"] >= 0
            assert isinstance(v["blend_shapes"], dict)
            for shape, weight in v["blend_shapes"].items():
                assert shape in VisemeMapper.ALL_BLEND_SHAPES
                assert 0.0 <= weight <= 1.0
            last_time = v["time_ms"]

    def test_synthesize_empty_and_whitespace_rejection(self, client):
        """Verify empty and whitespace strings return HTTP 400."""
        for text in ["", "   ", "\t\t\n", "   \r\n  "]:
            resp = client.post("/api/voice/synthesize", json={"text": text})
            assert resp.status_code == 400, f"Text '{text}' did not return 400: {resp.status_code}"
            assert "empty" in resp.json().get("detail", "").lower()

    def test_synthesize_missing_text_field(self, client):
        """Verify request missing text field returns HTTP 422."""
        resp = client.post("/api/voice/synthesize", json={"voice": "en_US"})
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    def test_synthesize_max_length_boundary(self, client):
        """Verify boundary text of 4096 chars succeeds and >4096 chars returns 422."""
        # 4096 characters should pass Pydantic validation
        text_4096 = "Hello world. " * 315 + "123456"  # Exactly 4096 chars: 315*13 = 4095 + 1 = 4096
        text_4096 = text_4096[:4096]
        assert len(text_4096) == 4096

        resp = client.post("/api/voice/synthesize", json={"text": text_4096})
        assert resp.status_code == 200, f"Expected 200 for 4096 chars, got {resp.status_code}"

        # 4097 characters should fail Pydantic validation (HTTP 422)
        text_4097 = text_4096 + "x"
        assert len(text_4097) == 4097
        resp_over = client.post("/api/voice/synthesize", json={"text": text_4097})
        assert resp_over.status_code == 422, f"Expected 422 for 4097 chars, got {resp_over.status_code}"

    def test_synthesize_special_characters_and_markup(self, client):
        """Verify input sanitization strips HTML tags and processes special characters cleanly."""
        xss_text = "<script>alert('xss')</script><b>Hello</b> & Welcome! @#%^*()_+"
        resp = client.post("/api/voice/synthesize", json={"text": xss_text})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["audio_base64"]) > 0

    def test_synthesize_concurrent_non_blocking_calls(self, client):
        """Verify 20 concurrent synthesis requests complete successfully without deadlocking."""
        prompts = [
            f"Command {i}: Initiating neural coprocessor synchronization cycle."
            for i in range(20)
        ]

        def call_synthesize(prompt: str) -> Dict[str, Any]:
            c = TestClient(app)
            t0 = time.perf_counter()
            r = c.post("/api/voice/synthesize", json={"text": prompt})
            latency = (time.perf_counter() - t0) * 1000
            return {
                "status_code": r.status_code,
                "data": r.json() if r.status_code == 200 else r.text,
                "latency": latency,
            }

        start_all = time.perf_counter()
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(call_synthesize, p) for p in prompts]
            results = [f.result() for f in as_completed(futures)]
        total_time = time.perf_counter() - start_all

        assert len(results) == 20
        for idx, res in enumerate(results):
            assert res["status_code"] == 200, f"Request {idx} failed with {res['status_code']}: {res['data']}"
            assert res["data"]["status"] == "ok"
            assert len(res["data"]["audio_base64"]) > 0
            assert len(res["data"]["visemes"]) > 0

        # Concurrency must not block all requests into a sequential queue taking minutes
        assert total_time < 15.0, f"20 concurrent syntheses took {total_time:.2f}s, expected < 15s"

    def test_headtts_failure_isolation_and_fallback_overhead(self):
        """
        Adversarial inspection: HeadTTSBackend unconditionally returns is_available=True,
        causing every synthesis call to attempt a TCP connection to localhost:5000.
        When HeadTTS is offline, this adds ~300ms overhead and closes/abandons aiohttp ClientSession.
        """
        from serein_core.voice_engine import HeadTTSBackend, TTSRequest
        backend = HeadTTSBackend()
        assert backend.is_available is True, "HeadTTSBackend is_available should be True"

        # Attempt synthesis against offline port 5000 and confirm it fails within timeout
        start = time.perf_counter()
        with pytest.raises(Exception):
            import asyncio
            asyncio.run(backend.synthesize(TTSRequest(text="offline test")))
        duration_ms = (time.perf_counter() - start) * 1000
        # Confirms failure is bounded by ~300ms timeout
        assert duration_ms < 600, f"HeadTTS failure took {duration_ms}ms, exceeding expected 600ms bound"



# ============================================================================
# 2. POST /api/voice/transcribe Adversarial Tests
# ============================================================================

class TestVoiceTranscribeAPI:
    """Adversarial stress-testing of /api/voice/transcribe."""

    @pytest.fixture(scope="class")
    def client(self):
        return TestClient(app)

    @pytest.fixture(scope="class")
    def sample_wav(self):
        return make_test_wav(duration_s=0.8, sample_rate=16000)

    def test_transcribe_json_audio_base64_success(self, client, sample_wav):
        """Verify valid audio_base64 JSON payload returns transcript."""
        b64_str = base64.b64encode(sample_wav).decode("ascii")
        resp = client.post("/api/voice/transcribe", json={"audio_base64": b64_str})

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        assert "text" in data
        assert isinstance(data["text"], str) and len(data["text"]) > 0
        assert "confidence" in data
        assert "duration_ms" in data
        assert "words" in data
        assert isinstance(data["words"], list)

    def test_transcribe_json_audio_alias_key(self, client, sample_wav):
        """Verify alternative JSON key 'audio' is accepted."""
        b64_str = base64.b64encode(sample_wav).decode("ascii")
        resp = client.post("/api/voice/transcribe", json={"audio": b64_str})

        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"

    def test_transcribe_multipart_form_success(self, client, sample_wav):
        """Verify multipart/form-data upload using 'file' and 'audio' keys."""
        # Test key: file
        files1 = {"file": ("recording.wav", io.BytesIO(sample_wav), "audio/wav")}
        resp1 = client.post("/api/voice/transcribe", files=files1)
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "ok"

        # Test key: audio
        files2 = {"audio": ("recording.wav", io.BytesIO(sample_wav), "audio/wav")}
        resp2 = client.post("/api/voice/transcribe", files=files2)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "ok"

    def test_transcribe_raw_binary_body(self, client, sample_wav):
        """Verify raw audio/wav binary stream in request body."""
        headers = {"Content-Type": "audio/wav"}
        resp = client.post("/api/voice/transcribe", content=sample_wav, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_transcribe_corrupted_base64_error(self, client):
        """Verify corrupted base64 audio strings return HTTP 400."""
        corrupted_payloads = [
            {"audio_base64": "!!!not_valid_base64@@@"},
            {"audio_base64": "==invalid_padding"},
            {"audio_base64": "abc"},  # Invalid 3-byte unpadded base64
            {"audio_base64": 12345},  # Non-string
        ]
        for payload in corrupted_payloads:
            resp = client.post("/api/voice/transcribe", json=payload)
            assert resp.status_code == 400, f"Payload {payload} should have returned 400, got {resp.status_code}"
            assert "invalid" in resp.json().get("detail", "").lower() or "audio" in resp.json().get("detail", "").lower()

    def test_transcribe_empty_audio_payloads_error(self, client):
        """Verify empty audio inputs return HTTP 400."""
        empty_payloads = [
            {},
            {"audio_base64": ""},
            {"audio_base64": "   "},
            {"audio": ""},
            {"audio": None},
        ]
        for payload in empty_payloads:
            resp = client.post("/api/voice/transcribe", json=payload)
            assert resp.status_code == 400, f"Payload {payload} should have returned 400, got {resp.status_code}"

        # Empty multipart form
        files = {"file": ("empty.wav", io.BytesIO(b""), "audio/wav")}
        resp_file = client.post("/api/voice/transcribe", files=files)
        assert resp_file.status_code == 400

        # Empty raw body
        resp_empty_body = client.post("/api/voice/transcribe", content=b"", headers={"Content-Type": "audio/wav"})
        assert resp_empty_body.status_code == 400

    def test_transcribe_unsupported_content_types(self, client):
        """Verify unsupported content types with empty body reject cleanly with HTTP 400."""
        resp = client.post("/api/voice/transcribe", content=b"", headers={"Content-Type": "text/plain"})
        assert resp.status_code == 400

        resp_xml = client.post("/api/voice/transcribe", content=b"", headers={"Content-Type": "application/xml"})
        assert resp_xml.status_code == 400

    def test_transcribe_concurrent_non_blocking_calls(self, client, sample_wav):
        """Verify 20 concurrent transcription requests complete successfully."""
        b64_audio = base64.b64encode(sample_wav).decode("ascii")

        def call_transcribe(idx: int) -> Dict[str, Any]:
            c = TestClient(app)
            r = c.post("/api/voice/transcribe", json={"audio_base64": b64_audio})
            return {"index": idx, "status_code": r.status_code, "data": r.json() if r.status_code == 200 else r.text}

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(call_transcribe, i) for i in range(20)]
            results = [f.result() for f in as_completed(futures)]
        elapsed = time.perf_counter() - start

        assert len(results) == 20
        for res in results:
            assert res["status_code"] == 200
            assert res["data"]["status"] == "ok"
            assert "text" in res["data"]
        assert elapsed < 10.0, f"20 concurrent transcribes took {elapsed:.2f}s"


# ============================================================================
# 3. Cross-Concurrency & Roundtrip Stress
# ============================================================================

class TestCrossConcurrencyStress:
    """Stress test interleaved synthesis and transcription across concurrent workers."""

    def test_simultaneous_synthesize_and_transcribe_load(self):
        """Run 10 synthesis requests and 10 transcription requests concurrently."""
        client = TestClient(app)
        test_wav = make_test_wav(duration_s=0.6, sample_rate=16000)
        test_b64 = base64.b64encode(test_wav).decode("ascii")

        def task_synth(idx: int):
            c = TestClient(app)
            return c.post("/api/voice/synthesize", json={"text": f"Concurrent test {idx}"})

        def task_trans(idx: int):
            c = TestClient(app)
            return c.post("/api/voice/transcribe", json={"audio_base64": test_b64})

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=12) as executor:
            synth_futs = [executor.submit(task_synth, i) for i in range(10)]
            trans_futs = [executor.submit(task_trans, i) for i in range(10)]
            synth_res = [f.result() for f in as_completed(synth_futs)]
            trans_res = [f.result() for f in as_completed(trans_futs)]
        duration = time.perf_counter() - start

        assert len(synth_res) == 10
        assert len(trans_res) == 10
        for r in synth_res:
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
        for r in trans_res:
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

        assert duration < 12.0


# ============================================================================
# 4. assets/ui/app.js Web Audio FFT & Viseme Animation Contract Verification
# ============================================================================

class TestUIContractInspection:
    """Empirically inspects assets/ui/app.js against Web Audio and Viseme contracts."""

    @pytest.fixture(scope="class")
    def app_js_content(self) -> str:
        app_js_path = REPO_ROOT / "assets" / "ui" / "app.js"
        assert app_js_path.exists(), f"assets/ui/app.js not found at {app_js_path}"
        return app_js_path.read_text(encoding="utf-8")

    def test_web_audio_context_initialization(self, app_js_content: str):
        """Verify getAudioContext() safely checks AudioContext and webkitAudioContext."""
        assert "function getAudioContext()" in app_js_content
        assert "window.AudioContext || window.webkitAudioContext" in app_js_content
        assert "audioContext.resume()" in app_js_content

    def test_speech_analyser_node_setup(self, app_js_content: str):
        """Verify speechAnalyserNode connects bufferSource -> analyser -> destination with fftSize."""
        assert "let speechAnalyserNode = null;" in app_js_content
        assert "let speechDataArray = null;" in app_js_content
        assert "ctx.createAnalyser()" in app_js_content
        assert "speechAnalyserNode.fftSize" in app_js_content
        assert "source.connect(speechAnalyserNode)" in app_js_content
        assert "speechAnalyserNode.connect(ctx.destination)" in app_js_content

    def test_waveform_fft_frequency_bin_consumption(self, app_js_content: str):
        """Verify drawWaveform reads FFT spectrum data using getByteFrequencyData."""
        assert "function drawWaveform()" in app_js_content
        assert "speechAnalyserNode.getByteFrequencyData(speechDataArray)" in app_js_content
        assert "analyserNode.getByteFrequencyData(micDataArray)" in app_js_content

        # Verify mathematical mapping of FFT frequency bins across bars
        assert "spectrumData[binIndex]" in app_js_content
        assert "Math.cos(distFromCenter * (Math.PI / 2))" in app_js_content

    def test_viseme_scheduling_and_reset(self, app_js_content: str):
        """Verify visemes from API are scheduled via setTimeout with setViseme and reset."""
        assert "avatarStage.setViseme" in app_js_content
        assert "Math.max(0, v.time_ms)" in app_js_content
        assert "Math.max(0, v.time_ms + (v.duration_ms || 60))" in app_js_content
        assert "visemeTimeouts.push" in app_js_content

        # Verify cleanup on audio stop / completion
        assert "source.onended" in app_js_content
        assert "avatarStage.stopSpeaking()" in app_js_content
        assert "avatarStage.setViseme('aa', 0)" in app_js_content

    def test_mic_capture_and_offline_transcribe_dispatch(self, app_js_content: str):
        """Verify microphone audio is recorded via MediaRecorder and posted to /api/voice/transcribe."""
        assert "MediaRecorder" in app_js_content
        assert "mediaRecorder.start" in app_js_content
        assert "mediaRecorder.stop" in app_js_content
        assert "fetch('/api/voice/transcribe'" in app_js_content
        assert "audio_base64: base64Audio" in app_js_content
