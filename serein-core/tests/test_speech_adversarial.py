"""
Adversarial Stress Testing Suite for SereinOS Speech Input Pipeline.
Empirical verification across:
1. Malformed PCM frames (odd bytes, zero-length, non-PCM payloads, massive chunks)
2. Pathological acoustic signals in VAD (DC offset, continuous noise, clicks, sweeps, clipping)
3. False alarm rate (FAR) in wake-word detection (pure tones, chords, adversarial envelopes, competing phrases)
4. Debounce refractory periods & timing boundaries
5. Async non-blocking execution under heavy churn & rapid lifecycles
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pytest

# Ensure serein-voice and serein-core/src are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VOICE_DIR = REPO_ROOT / "serein-voice"
CORE_SRC = REPO_ROOT / "serein-core" / "src"

for p in (str(VOICE_DIR), str(CORE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from audio_capture import (
    AudioCaptureLoop,
    AudioCaptureSource,
    AudioChunk,
    HardwareAudioCapture,
    HeadlessAudioSource,
    generate_silence_pcm,
    generate_sine_pcm,
)
from vad import UtteranceSegmenter, VADState, VoiceActivityDetector
from wake_word import (
    AcousticPatternDetector,
    OpenWakeWordBackend,
    WakeWordDetector,
    WakeWordResult,
    generate_wake_word_pcm,
)
from voice_engine import MockSTTBackend, VoiceEngine
from serein_core.events import CoreEvents, Event, UtteranceEventData, WakeWordEventData, event_bus


# ============================================================================
# 1. MALFORMED PCM FRAMES & STRESS TESTING
# ============================================================================

def test_adversarial_odd_byte_lengths_rejection():
    """Verify odd byte counts are systematically rejected with ValueError by feed_audio."""
    source = HeadlessAudioSource()
    odd_sizes = [1, 3, 7, 255, 1023, 2047, 2049, 4095, 65535]
    for size in odd_sizes:
        with pytest.raises(ValueError, match="Malformed PCM data: odd number of bytes"):
            source.feed_audio(b"\x00" * size)


@pytest.mark.asyncio
async def test_adversarial_engine_feed_odd_bytes_raises():
    """Verify VoiceEngine.feed_audio raises ValueError on odd byte counts."""
    engine = VoiceEngine()
    await engine.initialize()
    with pytest.raises(ValueError, match="Malformed PCM data"):
        engine.feed_audio(b"\x00" * 2047)
    await engine.stop()


def test_adversarial_vad_and_wake_gracefully_handle_odd_and_sub_sample_bytes():
    """Verify VAD and WakeWord do NOT crash or raise IndexError when fed odd or sub-sample bytes."""
    vad = VoiceActivityDetector()
    wake = WakeWordDetector()

    sub_sizes = [b"", b"\x00", b"\x01", b"\x00\x01\x02", b"\xff" * 5, b"\x00" * 7]
    for raw in sub_sizes:
        # VAD must not crash
        rms = vad.compute_rms(raw)
        assert isinstance(rms, float) and rms >= 0.0
        zcr = vad.compute_zcr(raw)
        assert isinstance(zcr, float) and zcr >= 0.0
        speech = vad.is_speech(raw)
        assert speech is False

        # UtteranceSegmenter must not crash
        segmenter = UtteranceSegmenter(vad=vad)
        res = segmenter.process_frame(raw)
        assert res is None

        # WakeWordDetector must not crash
        wake_res = wake.process_frame_sync(raw)
        assert isinstance(wake_res, WakeWordResult)
        assert wake_res.detected is False


def test_adversarial_random_garbage_and_extreme_sample_values():
    """Verify robust processing of random bytes and extreme min/max int16 values."""
    vad = VoiceActivityDetector()
    wake = WakeWordDetector()
    segmenter = UtteranceSegmenter(vad=vad)

    test_payloads = [
        # Max positive int16: 0x7FFF (+32767)
        b"\xff\x7f" * 1024,
        # Max negative int16: 0x8000 (-32768)
        b"\x00\x80" * 1024,
        # Alternating max positive and max negative (Nyquist oscillation)
        (b"\xff\x7f\x00\x80") * 512,
        # Pseudorandom byte sequence
        bytes([i % 256 for i in range(2048)]),
        # All 0xFF (raw -1 in int16)
        b"\xff" * 2048,
    ]

    for payload in test_payloads:
        # None of these should throw FloatingPointError or NaN
        rms = vad.compute_rms(payload)
        assert not np.isnan(rms) and not np.isinf(rms)
        zcr = vad.compute_zcr(payload)
        assert not np.isnan(zcr) and not np.isinf(zcr)

        wake_res = wake.process_frame_sync(payload)
        assert not np.isnan(wake_res.confidence)
        assert 0.0 <= wake_res.confidence <= 1.0

        seg_res = segmenter.process_frame(payload)
        assert seg_res is None or isinstance(seg_res, bytes)


def test_adversarial_massive_buffer_feed_and_slicing():
    """Verify feeding a massive 4MB chunk slices correctly into 1024-sample frames."""
    source = HeadlessAudioSource(chunk_size=1024)
    # 4MB = 4,194,304 bytes = 2,048 frames of 2048 bytes
    total_bytes = 4 * 1024 * 1024
    huge_pcm = b"\x01\x00" * (total_bytes // 2)

    source.feed_audio(huge_pcm)
    assert len(source._residual_buffer) == 0
    # Queue is bounded to max_queue_size (500), so queue size must be 500
    assert source._queue.qsize() == 500


# ============================================================================
# 2. PATHOLOGICAL ACOUSTIC SIGNALS IN VAD
# ============================================================================

def test_vad_pathological_dc_offset():
    """
    Verify pure DC offset (constant bias, zero AC variation) is rejected.
    VAD uses DC detrending and ZCR gating to prevent DC bias from being classified as speech.
    """
    vad = VoiceActivityDetector(energy_threshold=0.015)
    
    # Pure DC bias: constant value +2000 in int16 (~0.061 normalized float)
    dc_val = 2000
    dc_frame = np.full(1024, dc_val, dtype=np.int16).tobytes()

    rms = vad.compute_rms(dc_frame)
    zcr = vad.compute_zcr(dc_frame)
    is_sp = vad.is_speech(dc_frame)

    # Observation: zero zero-crossings because signal never crosses 0
    assert zcr == 0.0
    # Defense: RMS is detrended to 0.0, is_speech is False!
    assert rms < 0.015
    assert is_sp is False, "VAD correctly rejects pure DC offset"

    # Segmenter will NOT emit fake utterance
    segmenter = UtteranceSegmenter(vad=vad, max_speech_duration=0.512)
    emitted = None
    for _ in range(12):
        out = segmenter.process_frame(dc_frame)
        if out is not None:
            emitted = out
            break
    assert emitted is None, "Segmenter properly suppresses fake utterance on DC offset"


def test_vad_pathological_continuous_noise_adaptation_deadlock():
    """
    Verify stationary white noise above initial energy_threshold adapts the noise floor.
    Anti-deadlock percentile tracking ensures continuous noise floor adaptation.
    """
    vad = VoiceActivityDetector(energy_threshold=0.015, adaptation_rate=0.05)
    rng = np.random.RandomState(42)

    # Elevated ambient noise (RMS ~0.025, above default 0.015)
    noise_frame = (rng.normal(0, 0.025, 1024) * 32767).astype(np.int16).tobytes()
    initial_floor = vad.noise_floor

    # Feed 50 frames of continuous stationary noise
    for _ in range(50):
        vad.is_speech(noise_frame)

    # Defense: noise floor successfully adapted upward to track ambient noise floor
    assert vad.noise_floor > initial_floor, "Noise floor successfully adapted to continuous ambient noise"


def test_vad_pathological_clicks_and_impulses():
    """Verify isolated acoustic clicks/impulses < 0.4s are rejected by UtteranceSegmenter."""
    vad = VoiceActivityDetector(energy_threshold=0.015)
    segmenter = UtteranceSegmenter(
        vad=vad,
        silence_duration=0.32,  # 5 frames
        min_speech_duration=0.4,  # min 0.4s speech required
    )

    silence = generate_silence_pcm(duration_s=0.064)

    # 1. Single Dirac impulse frame (1 sample = 32767, rest 0)
    impulse = np.zeros(1024, dtype=np.int16)
    impulse[512] = 32767
    impulse_bytes = impulse.tobytes()

    segmenter.process_frame(silence)
    res1 = segmenter.process_frame(impulse_bytes)
    assert res1 is None
    res2 = segmenter.process_frame(silence)
    assert res2 is None
    assert segmenter.state == VADState.SILENCE

    # 2. Two-frame burst (~128ms, passes onset confirmation but < 0.4s min_speech_duration)
    burst_frame = generate_sine_pcm(duration_s=0.064, freq_hz=1000.0, amplitude=0.8)
    segmenter.process_frame(burst_frame)  # Frame 1 -> SPEECH_ONSET
    segmenter.process_frame(burst_frame)  # Frame 2 -> IN_SPEECH

    burst_result = None
    for _ in range(6):
        out = segmenter.process_frame(silence)
        if out is not None:
            burst_result = out

    assert burst_result is None
    assert segmenter.state == VADState.SILENCE


def test_vad_frequency_chirp_sweep():
    """Verify VAD behavior across frequency chirp sweeping from speech frequencies (200Hz-7000Hz) and mains hum (50Hz)."""
    vad = VoiceActivityDetector(energy_threshold=0.015)
    sample_rate = 16000
    duration_s = 0.064
    t = np.linspace(0, duration_s, 1024, endpoint=False)

    # 50Hz (AC mains hum) is below vocal pitch and rejected by ZCR gating
    hum_wave = (0.3 * np.sin(2.0 * np.pi * 50 * t) * 32767).astype(np.int16).tobytes()
    assert vad.compute_zcr(hum_wave) < 0.02, "50Hz mains hum has sub-speech ZCR"
    assert vad.is_speech(hum_wave) is False, "50Hz mains hum suppressed by ZCR gating"

    # Speech frequencies: 200Hz to 7000Hz correctly detected
    speech_frequencies = [200, 500, 1000, 2500, 4000, 7000]
    for freq in speech_frequencies:
        sine_wave = (0.3 * np.sin(2.0 * np.pi * freq * t) * 32767).astype(np.int16).tobytes()
        rms = vad.compute_rms(sine_wave)
        assert rms > 0.015, f"Frequency {freq}Hz failed RMS threshold: {rms}"
        assert vad.is_speech(sine_wave) is True, f"Frequency {freq}Hz not detected as speech"


# ============================================================================
# 3. FALSE ALARM RATE (FAR) IN WAKE-WORD DETECTION
# ============================================================================

def test_wake_word_false_alarm_on_pure_tone_leakage():
    """
    Verify Bug 4 defense: Pure tone at 550Hz is rejected due to
    Hann windowing eliminating FFT spectral leakage into F2 (1200-2400Hz).
    """
    detector = WakeWordDetector(wake_word="Hey Serein", sensitivity=0.5)
    tone_pcm = generate_sine_pcm(duration_s=0.064, freq_hz=550.0, amplitude=0.6)

    res = detector.process_frame_sync(tone_pcm)
    # The detector rejects the pure 550Hz tone!
    assert res.detected is False, "Pure 550Hz tone does not trigger wake word activation"
    assert res.confidence < 0.50


def test_wake_word_false_alarm_on_continuous_flat_chord():
    """
    Verify Bug 1 defense: Conjunctive confidence model and bimodal envelope check
    suppress continuous flat chords (500Hz + 1500Hz) with 0.0 envelope score.
    """
    detector = WakeWordDetector(wake_word="Hey Serein", sensitivity=0.5)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    chord = (0.3 * np.sin(2 * np.pi * 500 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t)).astype(np.float32)
    
    conf = detector.backend.evaluate_window(chord)
    assert conf < 0.50, f"Continuous flat chord reached confidence {conf} < 0.50"


def test_wake_word_false_alarm_on_single_burst():
    """
    Verify Bug 2 defense: Single 200ms tone burst achieves 0.0 envelope score
    because min(q_hey, q_serein) > silence_threshold requires both syllables.
    """
    detector = WakeWordDetector(wake_word="Hey Serein", sensitivity=0.5)
    total = int(1.152 * 16000)
    audio = np.zeros(total, dtype=np.float32)
    t = np.linspace(0, 0.2, 3200, endpoint=False)
    audio[:3200] = 0.5 * np.sin(2 * np.pi * 500 * t)
    pcm = (audio * 32767).astype(np.int16).tobytes()

    results = [detector.process_frame_sync(pcm[i:i+2048]) for i in range(0, len(pcm), 2048)]
    any_detected = any(r.detected for r in results)
    assert any_detected is False, "Single burst falsely triggered wake word activation"


def test_wake_word_premature_partial_window_evaluation():
    """
    Verify Bug 3 defense: WakeWordDetector waits for the rolling window to accumulate
    and does not evaluate prematurely on Frame 0 (64ms).
    """
    detector = WakeWordDetector(wake_word="Hey Serein", sensitivity=0.5)
    wake_pcm = generate_wake_word_pcm(duration_s=1.152)
    
    # Frame 0 is only 64ms of the utterance
    frame_0 = wake_pcm[:2048]
    res_0 = detector.process_frame_sync(frame_0)

    # Frame 0 does not trigger premature activation
    assert res_0.detected is False, "WakeWordDetector does not prematurely evaluate on Frame 0"


# ============================================================================
# 4. DEBOUNCE REFRACTORY PERIODS & TIMING
# ============================================================================

def test_debounce_exact_refractory_boundaries():
    """Verify debounce refractory period behaves deterministically at timing boundaries."""
    detector = WakeWordDetector(wake_word="Hey Serein", debounce_duration=1.5, sensitivity=0.5)
    wake_pcm = generate_wake_word_pcm(duration_s=1.152)

    def feed_utterance():
        results = []
        for i in range(0, len(wake_pcm), 2048):
            f = wake_pcm[i : i + 2048]
            if len(f) == 2048:
                results.append(detector.process_frame_sync(f))
        return any(r.detected for r in results), any(r.debounced for r in results)

    # 1st Trigger: t = now
    det1, deb1 = feed_utterance()
    assert det1 is True

    # Immediate 2nd feed (< 0.2s): MUST be debounced
    det2, deb2 = feed_utterance()
    assert det2 is False
    assert deb2 is True

    # Simulate clock passage to 1.0s (< 1.5s): MUST be debounced
    detector._last_trigger_time = time.time() - 1.0
    det3, deb3 = feed_utterance()
    assert det3 is False
    assert deb3 is True

    # Simulate clock passage to 1.6s (> 1.5s): MUST trigger
    detector._last_trigger_time = time.time() - 1.6
    det4, deb4 = feed_utterance()
    assert det4 is True


# ============================================================================
# 5. ASYNC NON-BLOCKING EXECUTION & HEAVY CHURN
# ============================================================================

@pytest.mark.asyncio
async def test_audio_capture_loop_heavy_churn_5000_frames():
    """
    Stress test AudioCaptureLoop with 5,000 frames injected in burst churn.
    Verifies event loop responsiveness, queue bounds, and absence of deadlock.
    """
    source = HeadlessAudioSource(chunk_size=1024)
    loop = AudioCaptureLoop(source=source, max_queue_size=50)
    await loop.start()

    max_event_loop_lag = 0.0

    async def lag_monitor():
        nonlocal max_event_loop_lag
        for _ in range(20):
            t0 = time.perf_counter()
            await asyncio.sleep(0.005)
            lag = (time.perf_counter() - t0) - 0.005
            if lag > max_event_loop_lag:
                max_event_loop_lag = lag

    monitor_task = asyncio.create_task(lag_monitor())

    # Burst inject 5,000 frames (10 MB of PCM)
    frame = b"\x00\x00" * 1024
    for _ in range(5000):
        source.feed_audio(frame)

    await asyncio.sleep(0.1)
    await monitor_task
    await loop.stop()

    # Bounded queue must never exceed 50
    assert loop.get_queue().qsize() <= 50
    # Dropped frames should reflect overflow protection
    assert loop.dropped_frames > 0
    # Event loop latency should remain low (< 50ms)
    assert max_event_loop_lag < 0.05, f"Event loop lagged by {max_event_loop_lag*1000:.1f}ms"


@pytest.mark.asyncio
async def test_rapid_start_stop_lifecycle_churn():
    """Stress test rapid start and stop cycles without resource leaks or crashes."""
    engine = VoiceEngine()
    await engine.initialize()

    for cycle in range(15):
        await engine.start()
        assert engine._running is True
        # Feed one frame
        engine.feed_audio(b"\x00\x00" * 1024)
        await asyncio.sleep(0.01)
        await engine.stop()
        assert engine._running is False
