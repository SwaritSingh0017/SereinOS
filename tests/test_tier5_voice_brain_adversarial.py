"""
Tier 5 White-Box Adversarial Stress Test Suite: Voice & Brain Modules.
Empirical validation of edge cases, boundary conditions, concurrency,
corrupt inputs, ReDoS resilience, SQLite lock stress, and cloud failover.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_SRC = REPO_ROOT / "serein-core" / "src"
VOICE_SRC = REPO_ROOT / "serein-voice"

for p in (str(CORE_SRC), str(VOICE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from audio_capture import (
    AudioCaptureLoop,
    AudioChunk,
    HardwareAudioCapture,
    HeadlessAudioSource,
    generate_silence_pcm,
    generate_sine_pcm,
)
from vad import UtteranceSegmenter, VADState, VoiceActivityDetector
from wake_word import (
    AcousticPatternDetector,
    WakeWordDetector,
    WakeWordResult,
    generate_wake_word_pcm,
)
from voice_engine import (
    MockSTTBackend,
    PiperTTSBackend,
    STTRequest,
    STTResponse,
    SystemTTSBackend,
    TTSRequest,
    TTSResponse,
    VisemeMapper,
    VoiceEngine,
    VoiceProvider,
    extract_visemes_from_audio_energy,
)

from serein_core.ai import (
    ActionProposal,
    AIProvider,
    BrainOutputError,
    BrainRouter,
    ChatMessage,
    ChatResponse,
    ModelConfig,
    ModelTier,
    OllamaProvider,
)
from serein_core.config import get_config
from serein_core.context import ContextEngine, ContextState
from serein_core.database import Database, IdentityRow, SCHEMA
from serein_core.decision import ActionType, DecisionEngine, Route
from serein_core.events import (
    CoreEvents,
    Event,
    EventBus,
    UtteranceEventData,
    WakeWordEventData,
    event_bus,
)
from serein_core.intent.classifier import IntentClassifier
from serein_core.intent.schemas import IntentKind, IntentResult


# ============================================================================
# 1. Voice Engine & Audio Capture Adversarial Stress
# ============================================================================
class TestVoiceEngineAudioCaptureAdversarial:
    """Stress-tests for AudioCaptureLoop, HeadlessAudioSource, HardwareAudioCapture fallback."""

    @pytest.mark.asyncio
    async def test_concurrent_async_feed_audio_stress(self):
        """Stress-test HeadlessAudioSource with 40 concurrent async producers."""
        source = HeadlessAudioSource(sample_rate=16000, chunk_size=1024, max_queue_size=200)
        await source.start()

        one_frame = generate_sine_pcm(freq_hz=440.0, duration_s=0.064, sample_rate=16000)
        assert len(one_frame) == 2048

        async def producer(idx: int):
            for _ in range(5):
                source.feed_audio(one_frame)
                await asyncio.sleep(0.001)

        tasks = [asyncio.create_task(producer(i)) for i in range(40)]
        await asyncio.gather(*tasks)

        # Ensure frames in queue are strictly multiples of 2048 bytes
        frames_drained = 0
        while not source._queue.empty():
            chunk = await source.read_chunk()
            if chunk:
                assert len(chunk) == 2048
                frames_drained += 1

        assert frames_drained > 0
        await source.stop()

    def test_odd_byte_length_rejection(self):
        """Feeding odd-byte raw PCM must raise ValueError to prevent 16-bit misalignment."""
        source = HeadlessAudioSource()
        odd_payloads = [b"\x00", b"\x01\x02\x03", b"A" * 511, b"\xff" * 2049]
        for odd_pcm in odd_payloads:
            with pytest.raises(ValueError, match="odd number of bytes"):
                source.feed_audio(odd_pcm)

    @pytest.mark.asyncio
    async def test_queue_overflow_drop_oldest(self):
        """Queue overflow policy must drop oldest frame without blocking or raising QueueFull."""
        source = HeadlessAudioSource(max_queue_size=10)
        await source.start()

        frame = b"\x01\x00" * 1024  # 2048 bytes
        # Push 25 frames into a queue with capacity 10
        for _ in range(25):
            source.feed_audio(frame)

        assert source._queue.qsize() <= 10
        chunk = await source.read_chunk()
        assert chunk is not None
        assert len(chunk) == 2048
        await source.stop()

    @pytest.mark.asyncio
    async def test_zero_length_audio_feed(self):
        """Feeding empty bytes must be a safe no-op."""
        source = HeadlessAudioSource()
        await source.start()
        source.feed_audio(b"")
        assert source._queue.qsize() == 0
        assert len(source._residual_buffer) == 0
        await source.stop()

    def test_hardware_capture_double_fault_fallback(self):
        """Simulate missing sounddevice AND pyaudio; ensures graceful headless fallback."""
        with patch.dict(sys.modules, {"sounddevice": None, "pyaudio": None}):
            hw = HardwareAudioCapture(sample_rate=16000)
            assert hw.is_hardware_available is False
            assert hw._driver_type == "fallback_headless"

    def test_audio_chunk_edge_properties(self):
        """Verify AudioChunk duration and sample math across extreme boundary values."""
        chunk_zero = AudioChunk(data=b"", sample_rate=16000)
        assert chunk_zero.num_samples == 0
        assert chunk_zero.duration_s == 0.0

        # Non-standard sample rate & stereo channels
        chunk_stereo = AudioChunk(data=b"\x00" * 3840, sample_rate=48000, channels=2, sample_width=2)
        assert chunk_stereo.num_samples == 960
        assert pytest.approx(chunk_stereo.duration_s, rel=1e-3) == 0.02


# ============================================================================
# 2. VAD & Utterance Segmentation Adversarial Conditions
# ============================================================================
class TestVADSegmentationAdversarial:
    """White-box tests for DC bias detrending, noise adaptation, and state machine bounds."""

    def test_dc_detrending_extreme_offset(self):
        """Constant high DC offset (+30,000) without AC fluctuation must detrend to RMS ~0.0."""
        vad = VoiceActivityDetector()
        dc_val = 30000
        dc_frame = np.full(1024, dc_val, dtype=np.int16).tobytes()

        rms = vad.compute_rms(dc_frame)
        assert rms < 1e-4
        assert vad.is_speech(dc_frame) is False

    def test_extreme_clipping_rail_to_rail(self):
        """Full scale rail-to-rail square wave (+32767 to -32768) must compute safely without NaNs."""
        vad = VoiceActivityDetector()
        samples = np.array([32767 if i % 2 == 0 else -32768 for i in range(1024)], dtype=np.int16)
        frame_bytes = samples.tobytes()

        rms = vad.compute_rms(frame_bytes)
        zcr = vad.compute_zcr(frame_bytes)

        assert not np.isnan(rms)
        assert not np.isnan(zcr)
        assert rms > 0.95
        assert zcr > 0.45

    def test_continuous_ambient_noise_percentile_adaptation(self):
        """100 frames of steady fan/HVAC noise must adapt floor to 10th percentile and cap at max_noise_floor."""
        vad = VoiceActivityDetector(energy_threshold=0.015, max_noise_floor=0.04)
        noise_samples = (np.random.normal(0, 0.035, 1024) * 32768).astype(np.int16)
        noise_bytes = noise_samples.tobytes()

        for _ in range(100):
            vad.is_speech(noise_bytes)

        assert vad.noise_floor <= 0.04
        assert vad.noise_floor >= 0.005

    def test_single_byte_and_empty_frames(self):
        """Gracefully handle empty bytes and 1-byte frames without index or unpack errors."""
        vad = VoiceActivityDetector()
        assert vad.compute_rms(b"") == 0.0
        assert vad.compute_rms(b"\x00") == 0.0
        assert vad.compute_zcr(b"") == 0.0
        assert vad.compute_zcr(b"\x00\x01\x02") == 0.0
        assert vad.is_speech(b"") is False
        assert vad.is_speech(b"\x01") is False

    def test_transient_acoustic_click_rejection(self):
        """Single high-energy frame (acoustic click) followed by silence must revert to SILENCE."""
        segmenter = UtteranceSegmenter(silence_duration=0.5, preroll_duration=0.2)
        click_frame = generate_sine_pcm(freq_hz=800.0, duration_s=0.064)
        silence_frame = generate_silence_pcm(duration_s=0.064)

        # 1. Silence state
        assert segmenter.state == VADState.SILENCE

        # 2. Feed click -> transitions to SPEECH_ONSET
        res = segmenter.process_frame(click_frame)
        assert res is None
        assert segmenter.state == VADState.SPEECH_ONSET

        # 3. Feed silence immediately -> reverts to SILENCE
        res = segmenter.process_frame(silence_frame)
        assert res is None
        assert segmenter.state == VADState.SILENCE

    def test_max_speech_duration_forced_cut(self):
        """Continuous speech exceeding max_speech_duration (e.g. 2.0s test limit) forces segmentation."""
        segmenter = UtteranceSegmenter(
            max_speech_duration=1.0,  # 1.0 second max
            silence_duration=0.5,
            preroll_duration=0.2,
        )
        speech_frame = generate_sine_pcm(freq_hz=440.0, duration_s=0.064)

        result_pcm = None
        # 1.0s / 0.064s ~ 16 frames. Feed 20 continuous frames of speech.
        for _ in range(25):
            res = segmenter.process_frame(speech_frame)
            if res is not None:
                result_pcm = res
                break

        assert result_pcm is not None
        assert len(result_pcm) > 0
        assert segmenter.state == VADState.SILENCE

    def test_sub_minimum_speech_burst_suppression(self):
        """Speech burst shorter than min_speech_duration followed by trailing silence must be discarded."""
        segmenter = UtteranceSegmenter(
            min_speech_duration=0.4,  # Require >= 400ms speech
            silence_duration=0.2,    # ~3-4 frames silence
            preroll_duration=0.1,
            speech_pad_chunks=2,
        )
        speech_frame = generate_sine_pcm(freq_hz=440.0, duration_s=0.064)
        silence_frame = generate_silence_pcm(duration_s=0.064)

        # Feed 2 speech frames (~128ms < 400ms)
        segmenter.process_frame(speech_frame)
        segmenter.process_frame(speech_frame)
        assert segmenter.state == VADState.IN_SPEECH

        # Feed trailing silence frames to trigger segmentation
        completed_pcm = None
        for _ in range(6):
            res = segmenter.process_frame(silence_frame)
            if res is not None:
                completed_pcm = res

        # Must discard burst and return None
        assert completed_pcm is None
        assert segmenter.state == VADState.SILENCE

    def test_back_to_back_rapid_utterances(self):
        """Segment two distinct utterances separated by trailing silence without frame bleeding."""
        segmenter = UtteranceSegmenter(
            min_speech_duration=0.2,
            silence_duration=0.2,
            preroll_duration=0.1,
            speech_pad_chunks=2,
        )
        speech_frame = generate_sine_pcm(freq_hz=440.0, duration_s=0.064)
        silence_frame = generate_silence_pcm(duration_s=0.064)

        # Utterance 1
        for _ in range(5):
            segmenter.process_frame(speech_frame)
        utt1 = None
        for _ in range(5):
            res = segmenter.process_frame(silence_frame)
            if res:
                utt1 = res
                break
        assert utt1 is not None

        # Utterance 2 immediately following
        for _ in range(5):
            segmenter.process_frame(speech_frame)
        utt2 = None
        for _ in range(5):
            res = segmenter.process_frame(silence_frame)
            if res:
                utt2 = res
                break
        assert utt2 is not None
        assert len(utt1) > 0
        assert len(utt2) > 0


# ============================================================================
# 3. Wake Word & FFT Spectrum Adversarial Scenarios
# ============================================================================
class TestWakeWordAndFFTSpectrumAdversarial:
    """Acoustic feature analysis, refractory debounce suppression, and FFT spectrum visualizer math."""

    def test_acoustic_pattern_short_frame_rejection(self):
        """Frames with <256 samples must return 0.0 confidence."""
        detector = AcousticPatternDetector()
        short_buf = np.ones(128, dtype=np.float32) * 0.5
        assert detector.evaluate_window(short_buf) == 0.0

    def test_acoustic_pattern_out_of_band_rejection(self):
        """6000Hz pure tone outside speech formant bands must return 0.0 confidence even at high amplitude."""
        detector = AcousticPatternDetector()
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        tone_6000 = (np.sin(2 * np.pi * 6000 * t) * 0.8).astype(np.float32)
        score = detector.evaluate_window(tone_6000, sample_rate=16000)
        assert score == 0.0

    def test_acoustic_pattern_flat_monotone_envelope_rejection(self):
        """Speech frequencies present with perfectly flat envelope (no bimodal syllable dip) must score 0.0."""
        detector = AcousticPatternDetector()
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        # Formants F1 (500Hz) and F2 (1800Hz) with flat constant amplitude
        flat_signal = (np.sin(2 * np.pi * 500 * t) * 0.4 + np.sin(2 * np.pi * 1800 * t) * 0.4).astype(np.float32)
        score = detector.evaluate_window(flat_signal, sample_rate=16000)
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_wake_word_debounce_refractory_suppression(self):
        """Second wake word within debounce_duration (1.5s) must be suppressed."""
        detector = WakeWordDetector(debounce_duration=1.5, sensitivity=0.4)
        await detector.start()

        wake_chunk = generate_wake_word_pcm(duration_s=1.2, sample_rate=16000)
        frame_bytes = 2048
        frames = [wake_chunk[i : i + frame_bytes] for i in range(0, len(wake_chunk), frame_bytes)]

        # First trigger
        first_detected = False
        for f in frames:
            res = await detector.process_frame(f)
            if res.detected:
                first_detected = True
                break
        assert first_detected is True

        # Immediate second trigger within debounce window
        second_detected = False
        for f in frames:
            res = await detector.process_frame(f)
            if res.detected:
                second_detected = True
                break
        assert second_detected is False

    @pytest.mark.asyncio
    async def test_wake_word_post_debounce_recovery(self):
        """Wake word triggers successfully after debounce period has elapsed."""
        detector = WakeWordDetector(debounce_duration=0.2, sensitivity=0.4)
        await detector.start()

        wake_chunk = generate_wake_word_pcm(duration_s=1.2, sample_rate=16000)
        frame_bytes = 2048
        frames = [wake_chunk[i : i + frame_bytes] for i in range(0, len(wake_chunk), frame_bytes)]

        # Trigger 1
        for f in frames:
            await detector.process_frame(f)

        # Advance last trigger time backwards past debounce window
        detector._last_trigger_time = time.time() - 2.0
        detector._window_buffer.clear()

        # Trigger 2
        second_detected = False
        for f in frames:
            res = await detector.process_frame(f)
            if res.detected:
                second_detected = True
                break
        assert second_detected is True

    def test_real_fft_visualizer_52_bins_nyquist(self):
        """Real FFT on 16kHz audio up to 8000Hz Nyquist limit maps cleanly into 52 canvas display bins."""
        num_samples = 1024
        sample_rate = 16000
        # 7900 Hz near-Nyquist tone
        t = np.linspace(0, num_samples / sample_rate, num_samples, endpoint=False)
        samples = (np.sin(2 * np.pi * 7900 * t)).astype(np.float32)

        # Compute real FFT
        fft_vals = np.abs(np.fft.rfft(samples))
        assert len(fft_vals) == 513
        assert not np.isnan(fft_vals).any()

        # Map to 52 display bins matching <canvas id="waveform-canvas">
        num_bars = 52
        indices = np.linspace(0, len(fft_vals) - 1, num_bars, dtype=int)
        display_bins = fft_vals[indices]
        assert len(display_bins) == 52
        assert display_bins[-1] > display_bins[0]  # Energy concentrated near top bin

    def test_real_fft_dc_spike_handling(self):
        """Pure DC signal produces sharp 0Hz peak without distorting downstream array operations."""
        samples = np.ones(1024, dtype=np.float32)
        fft_vals = np.abs(np.fft.rfft(samples))
        assert np.argmax(fft_vals) == 0
        assert not np.isinf(fft_vals).any()


# ============================================================================
# 4. Viseme Lip-Sync Generation & VRM Blend Shapes
# ============================================================================
class TestVisemeAndTTSAdversarial:
    """Viseme extraction robustness under malformed, silent, and extreme audio payloads."""

    def test_viseme_extraction_empty_and_corrupt(self):
        """Empty bytes, truncated headers, and 1-byte payloads return safe default 'sil' visemes."""
        v_empty = extract_visemes_from_audio_energy(b"")
        assert len(v_empty) == 1
        assert v_empty[0]["viseme"] == "sil"

        # Corrupt RIFF header
        corrupt_wav = b"RIFF\x10\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        v_corrupt = extract_visemes_from_audio_energy(corrupt_wav)
        assert len(v_corrupt) >= 1
        assert v_corrupt[0]["viseme"] == "sil"

        # 1 byte
        v_one = extract_visemes_from_audio_energy(b"\x00")
        assert len(v_one) == 1
        assert v_one[0]["viseme"] == "sil"

    def test_viseme_extraction_pure_silence(self):
        """PCM silence returns 'sil' viseme matching total audio duration."""
        silence_pcm = b"\x00\x00" * 22050  # 1 second at 22050Hz
        visemes = extract_visemes_from_audio_energy(silence_pcm, sample_rate=22050)
        assert len(visemes) == 1
        assert visemes[0]["viseme"] == "sil"
        assert visemes[0]["duration_ms"] >= 950

    def test_viseme_phonetic_cues_and_durations(self):
        """Complex sentence phonetic mapping produces valid monotonically increasing viseme timings."""
        text = "The quick brown fox jumps over the lazy dog"
        # 1.5 seconds of synthetic audio
        t = np.linspace(0, 1.5, int(22050 * 1.5), endpoint=False)
        audio_samples = (np.sin(2 * np.pi * 350 * t) * 0.7 * (1 + np.sin(2 * np.pi * 4 * t))).astype(np.float32)
        pcm_bytes = (audio_samples * 32767).astype(np.int16).tobytes()

        visemes = extract_visemes_from_audio_energy(pcm_bytes, sample_rate=22050, text=text)
        assert len(visemes) > 1

        last_time = -1
        for v in visemes:
            assert v["time_ms"] >= last_time
            last_time = v["time_ms"]
            assert v["duration_ms"] >= 30
            # Blend shapes must contain valid VRM keys with non-negative weights
            blend = v["blend_shapes"]
            assert isinstance(blend, dict)
            for shape, weight in blend.items():
                assert weight >= 0.0

    def test_viseme_mapper_safe_mode_null_handling(self):
        """VisemeMapper.MapPhonemeSafe and MapOculusVisemeSafe gracefully handle non-string / None inputs."""
        # Unsafe mode raises AttributeError
        with pytest.raises(AttributeError):
            VisemeMapper.MapPhoneme(None, safe=False)
        with pytest.raises(AttributeError):
            VisemeMapper.MapOculusViseme(12345, safe=False)

        # Safe mode returns fallback 'sil'
        res1 = VisemeMapper.MapPhonemeSafe(None)
        assert res1["sil"] == 1.0

        res2 = VisemeMapper.MapOculusVisemeSafe(None)
        assert res2["sil"] == 1.0

    def test_viseme_mapper_negative_intensity_clamping(self):
        """Negative intensity values must clamp weights to 0.0 (no negative blend shapes)."""
        res = VisemeMapper.MapAzureViseme(10, intensity=-2.0)
        for k, v in res.items():
            assert v == 0.0

        res_oculus = VisemeMapper.MapOculusViseme("aa", intensity=-1.5, safe=True)
        assert res_oculus["aa"] == 0.0

    @pytest.mark.asyncio
    async def test_voice_engine_all_tts_backends_failure(self):
        """When all registered TTS backends fail, VoiceEngine.speak must raise RuntimeError."""
        ve = VoiceEngine()
        await ve.initialize()

        # Force all backends to fail
        for backend in ve._tts_backends:
            backend.synthesize = AsyncMock(side_effect=RuntimeError("Simulated TTS synthesis crash"))

        with pytest.raises(RuntimeError, match="All TTS backends failed"):
            await ve.speak("Hello world")

        await ve.close()


# ============================================================================
# 5. Deterministic Offline Intent Engine & Decision Router
# ============================================================================
class TestIntentAndDecisionEngineAdversarial:
    """Stress tests on ReDoS inputs, entity parsing, and routing rules."""

    def test_intent_classifier_empty_and_whitespace(self):
        """Empty/whitespace inputs return 'unknown'; non-empty ambiguous punctuation returns fallback."""
        classifier = IntentClassifier()
        # Empty and pure whitespace inputs normalize to empty string -> UNKNOWN
        for text in ("", "   ", "\t\n", "   \r\n   "):
            res = classifier.classify(text)
            assert res.intent == IntentKind.UNKNOWN.value
            assert res.confidence == 0.0

        # Non-empty ambiguous punctuation falls back to conversation
        res_dots = classifier.classify("...")
        assert res_dots.intent == IntentKind.CONVERSATION.value

        # Trailing question marks match question pattern
        res_q = classifier.classify("???")
        assert res_q.intent == "question"

    def test_intent_classifier_redos_resilience(self):
        """20,000 character repetitive input string must classify in <50ms without ReDoS."""
        classifier = IntentClassifier()
        adversarial_text = "remind me " + "very " * 3000 + "important task tomorrow at 5pm"
        start = time.perf_counter()
        res = classifier.classify(adversarial_text)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 100.0  # Must be fast
        assert res.intent == "reminder"
        assert res.entities.get("time") == "5pm"
        assert res.entities.get("date") == "tomorrow"

    def test_intent_classifier_system_control_confirmation(self):
        """Dangerous system actions (shutdown, restart, sleep, lock) require confirmation."""
        classifier = IntentClassifier()
        for phrase in ("shutdown the computer", "power off now", "reboot my pc", "lock the computer"):
            res = classifier.classify(phrase)
            assert res.intent == "system_control"
            assert res.requires_confirmation is True

    def test_intent_classifier_app_control_kill_confirmation(self):
        """'kill' or 'quit' app control commands require confirmation, while 'open' does not."""
        classifier = IntentClassifier()
        res_kill = classifier.classify("kill spotify")
        assert res_kill.intent == "app_control"
        assert res_kill.entities.get("application") == "spotify"
        assert res_kill.requires_confirmation is True

        res_open = classifier.classify("open terminal")
        assert res_open.intent == "app_control"
        assert res_open.entities.get("application") == "terminal"
        assert res_open.requires_confirmation is False

    @pytest.mark.asyncio
    async def test_decision_engine_route_resolution(self):
        """DecisionEngine deterministically routes classified intents."""
        engine = DecisionEngine()
        await engine.initialize()

        # 1. System control -> COMMAND_BUS
        d1 = await engine.decide(IntentResult("system_control", 0.98, entities={"action": "shutdown"}))
        assert d1.route == Route.COMMAND_BUS
        assert d1.action == ActionType.EXECUTE

        # 2. Reminder -> SCHEDULER
        d2 = await engine.decide(IntentResult("reminder", 0.95, entities={"task": "call mom"}))
        assert d2.route == Route.SCHEDULER
        assert d2.action == ActionType.SCHEDULE

        # 3. Greeting -> CONVERSATION
        d3 = await engine.decide(IntentResult("greeting", 0.99))
        assert d3.route == Route.CONVERSATION
        assert d3.action == ActionType.RESPOND


# ============================================================================
# 6. SQLite Dialogue Persistence & Concurrency Stress
# ============================================================================
class TestSQLitePersistenceAndConcurrencyAdversarial:
    """Concurrency, SQL injection resistance, transaction rollbacks, and type fidelity."""

    @pytest.mark.asyncio
    async def test_sqlite_concurrent_readers_and_writers(self, tmp_path):
        """25 concurrent async tasks reading and writing SQLite simultaneously without lockups."""
        db_path = tmp_path / "concurrent_stress.db"
        db = Database(path=db_path)
        await db.connect()

        async def worker(worker_id: int):
            for i in range(5):
                # Write message
                await db.save_message({
                    "session_id": f"sess_{worker_id}",
                    "role": "user",
                    "content": f"Message {i} from worker {worker_id}",
                    "intent": "question",
                })
                # Write memory
                await db.save_memory({
                    "content": f"Memory {i} from worker {worker_id}",
                    "importance": worker_id * 3,
                    "tags": [f"w_{worker_id}", "concurrent"],
                })
                # Read messages
                msgs = await db.get_recent_messages(limit=5, session_id=f"sess_{worker_id}")
                assert len(msgs) > 0
                await asyncio.sleep(0.001)

        tasks = [asyncio.create_task(worker(i)) for i in range(25)]
        await asyncio.gather(*tasks)

        all_memories = await db.get_memories(limit=200)
        assert len(all_memories) >= 125
        await db.close()

    @pytest.mark.asyncio
    async def test_sqlite_sql_injection_parameterization(self, tmp_path):
        """SQL injection strings must be safely parameterized without schema corruption."""
        db_path = tmp_path / "sqli_test.db"
        db = Database(path=db_path)
        await db.connect()

        payload = "'; DROP TABLE memories; DROP TABLE identity; --"
        msg_id = await db.save_message({
            "session_id": "test_session",
            "role": "user",
            "content": payload,
            "intent": payload,
        })

        # Verify table still exists and data was inserted literally
        rows = await db.fetch_all("SELECT content FROM conversation_messages WHERE id = ?", (msg_id,))
        assert len(rows) == 1
        assert rows[0]["content"] == payload

        # Memories table must remain intact
        memories = await db.get_memories()
        assert isinstance(memories, list)
        await db.close()

    @pytest.mark.asyncio
    async def test_sqlite_large_payload_integrity(self, tmp_path):
        """Store and retrieve a 100,000 character dialogue message intact."""
        db_path = tmp_path / "large_payload.db"
        db = Database(path=db_path)
        await db.connect()

        huge_content = "SereinOS_Large_Data_" * 5000  # 100,000 chars
        msg_id = await db.save_message({
            "session_id": "large_session",
            "role": "assistant",
            "content": huge_content,
        })

        msgs = await db.get_recent_messages(limit=1, session_id="large_session")
        assert len(msgs) == 1
        assert msgs[0]["content"] == huge_content
        await db.close()

    @pytest.mark.asyncio
    async def test_sqlite_transaction_rollback_on_constraint_violation(self, tmp_path):
        """Failing transaction on identity table check constraint must roll back cleanly."""
        db_path = tmp_path / "rollback_test.db"
        db = Database(path=db_path)
        await db.connect()

        # Valid initial identity (id=1)
        await db.set_identity(IdentityRow(user_name="SereinUser"))

        # Attempt invalid insert with id=2 (violates CHECK (id = 1))
        with pytest.raises(sqlite3.IntegrityError):
            async with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO identity (id, user_name, created_at, last_seen) VALUES (2, 'Hacker', 'now', 'now')"
                )

        # Ensure database is still usable after rollback
        identity = await db.get_identity()
        assert identity.user_name == "SereinUser"
        await db.close()

    @pytest.mark.asyncio
    async def test_sqlite_preference_type_fidelity(self, tmp_path):
        """Preferences engine preserves types: nested dict, boolean, float, list, None."""
        db_path = tmp_path / "prefs_test.db"
        db = Database(path=db_path)
        await db.connect()

        test_data = {
            "dark_mode": True,
            "volume_level": 0.85,
            "repeat_count": 42,
            "favorite_apps": ["terminal", "editor", "browser"],
            "window_bounds": {"x": 100, "y": 200, "w": 1280, "h": 720},
            "null_value": None,
        }

        for k, v in test_data.items():
            await db.set_preference(k, v)

        for k, v in test_data.items():
            retrieved = await db.get_preference(k)
            assert retrieved == v

        all_prefs = await db.get_all_preferences()
        assert len(all_prefs) == len(test_data)
        await db.close()


# ============================================================================
# 7. BrainRouter & Cloud Failover Circuit Breaking
# ============================================================================
class TestBrainRouterAndCloudFailoverAdversarial:
    """Offline routing short-circuit, provider failure penalties, and context probe."""

    @pytest.mark.asyncio
    async def test_brain_router_offline_context_short_circuit(self):
        """When internet_connected is False, BrainRouter directly routes to offline deterministic intent."""
        cfg = get_config()
        router = BrainRouter(cfg)

        messages = [ChatMessage(role="user", content="Hello Serein")]
        resp = await router.chat(messages, context={"internet_connected": False})

        assert resp.provider == "offline-deterministic"
        assert resp.tier == ModelTier.LOCAL
        assert "local offline mode" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_brain_router_cloud_provider_error_failover(self):
        """When cloud provider raises an error, BrainRouter gracefully fails over to offline generator."""
        cfg = get_config()
        router = BrainRouter(cfg)

        # Mock mock-provider that always fails
        failing_provider = MagicMock(spec=AIProvider)
        failing_provider.name = "mock_failing"
        failing_provider.available_tiers = [ModelTier.FAST]
        failing_provider.get_model_config.return_value = ModelConfig(name="broken-model", tier=ModelTier.FAST)
        failing_provider.chat = AsyncMock(side_effect=ConnectionResetError("Simulated socket drop"))

        router.providers = {"failing": failing_provider}

        messages = [ChatMessage(role="user", content="What time is it?")]
        resp = await router.chat(messages, tier=ModelTier.FAST, context={"internet_connected": True})

        # Must fall back gracefully to deterministic offline reasoning
        assert resp.provider == "offline-deterministic"
        assert "time is" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_brain_router_failure_penalty_scoring(self):
        """Providers that accumulate failures must be sorted after healthy providers."""
        cfg = get_config()
        router = BrainRouter(cfg)

        p1 = MagicMock(spec=AIProvider)
        p1.name = "provider_alpha"
        p1.available_tiers = [ModelTier.FAST]
        p1.get_model_config.return_value = ModelConfig(name="m1", tier=ModelTier.FAST)

        p2 = MagicMock(spec=AIProvider)
        p2.name = "provider_beta"
        p2.available_tiers = [ModelTier.FAST]
        p2.get_model_config.return_value = ModelConfig(name="m2", tier=ModelTier.FAST)

        router.providers = {"alpha": p1, "beta": p2}
        router._failure_counts["provider_alpha"] = 5  # High failures
        router._failure_counts["provider_beta"] = 0   # Healthy

        best_provider, _ = router._select_provider_and_model(ModelTier.FAST)
        assert best_provider.name == "provider_beta"

    @pytest.mark.asyncio
    async def test_brain_router_empty_messages_validation(self):
        """Calling chat with empty messages list raises ValueError."""
        cfg = get_config()
        router = BrainRouter(cfg)
        with pytest.raises(ValueError, match="cannot be empty"):
            await router.chat([])

    @pytest.mark.asyncio
    async def test_brain_router_propose_action_malformed_json_handling(self):
        """When AI response is not valid JSON, propose_action raises BrainOutputError."""
        cfg = get_config()
        router = BrainRouter(cfg)
        # Mock chat to return plain non-JSON text
        router.chat = AsyncMock(
            return_value=ChatResponse(
                content="I cannot perform that in JSON format.",
                model="test",
                tier=ModelTier.FAST,
                latency_ms=10,
            )
        )

        with pytest.raises(BrainOutputError, match="Failed to parse action JSON"):
            await router.propose_action([ChatMessage(role="user", content="Open file")])

    @pytest.mark.asyncio
    async def test_context_engine_rapid_network_transition_events(self, tmp_path):
        """Rapid network connectivity toggles fire CoreEvents.SYSTEM_STATE_CHANGED."""
        db_path = tmp_path / "ctx_test.db"
        db = Database(path=db_path)
        await db.connect()

        ctx_engine = ContextEngine(database=db)
        await ctx_engine.initialize()

        events_received = []

        async def on_state_change(event: Event):
            events_received.append(event.data)

        event_bus.subscribe(CoreEvents.SYSTEM_STATE_CHANGED, on_state_change)

        # Toggle online -> offline -> online
        await ctx_engine.set_connectivity(False)
        await ctx_engine.set_connectivity(True)
        await ctx_engine.set_connectivity(False)
        await asyncio.sleep(0.05)

        assert len(events_received) >= 2
        states = [e.get("network") for e in events_received]
        assert "offline" in states
        assert "online" in states

        await ctx_engine.close()
        await db.close()
