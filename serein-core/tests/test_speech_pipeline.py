"""
Automated Pytest Verification Suite for SereinOS Speech Input Pipeline.
Covers R1-R4 across:
- Tier 1: Feature Coverage (AudioCaptureLoop, HeadlessAudioSource, VAD, UtteranceSegmenter, WakeWord, EventBus)
- Tier 2: Boundary & Corner Cases (Odd PCM lengths, queue overflow, noise spikes, duration guardrails, debounce)
- Tier 3: Cross-Feature Combinations (Capture -> VAD -> STT -> EventBus, concurrency, lifecycle)
- Tier 4: Real-World Workload Scenarios (Full conversations, prolonged silence, rapid successive speech)
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import List

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
from voice_engine import MockSTTBackend, STTRequest, VoiceEngine
from serein_core.config import VoiceConfig, get_config
from serein_core.events import (
    CoreEvents,
    Event,
    UtteranceEventData,
    WakeWordEventData,
    event_bus,
)


@pytest.fixture(autouse=True)
def clean_event_bus():
    """Ensure clean event bus subscribers and history between tests."""
    from collections import defaultdict

    event_bus.clear_history()
    old_subs = defaultdict(list, {k: list(v) for k, v in event_bus._subscribers.items()})
    old_typed = defaultdict(list, {k: list(v) for k, v in event_bus._typed_subscribers.items()})
    event_bus._subscribers.clear()
    event_bus._typed_subscribers.clear()
    yield
    event_bus.clear_history()
    event_bus._subscribers = old_subs
    event_bus._typed_subscribers = old_typed


# ============================================================================
# TIER 1: FEATURE COVERAGE
# ============================================================================

def test_tier1_audio_chunk_properties():
    """Verify AudioChunk sample math and duration calculation."""
    # 1024 samples of 16-bit mono = 2048 bytes at 16kHz -> 64ms duration
    data = b"\x00" * 2048
    chunk = AudioChunk(data=data, sample_rate=16000, channels=1)
    assert chunk.num_samples == 1024
    assert abs(chunk.duration_s - 0.064) < 1e-4
    assert chunk.sample_width == 2


@pytest.mark.asyncio
async def test_tier1_headless_audio_source_feed_and_stream():
    """Verify HeadlessAudioSource frame slicing and streaming."""
    source = HeadlessAudioSource(sample_rate=16000, chunk_size=1024)
    await source.start()

    # Feed 2 frames (4096 bytes)
    raw_pcm = generate_sine_pcm(duration_s=0.128, freq_hz=440.0)
    assert len(raw_pcm) == 4096
    source.feed_audio(raw_pcm)

    chunk1 = await source.read_chunk()
    chunk2 = await source.read_chunk()

    assert chunk1 is not None and len(chunk1) == 2048
    assert chunk2 is not None and len(chunk2) == 2048

    await source.stop()


@pytest.mark.asyncio
async def test_tier1_audio_capture_loop_non_blocking_execution():
    """Verify AudioCaptureLoop operates asynchronously without blocking the event loop."""
    source = HeadlessAudioSource(chunk_size=1024)
    loop = AudioCaptureLoop(source=source)
    task = await loop.start()

    # Verify background execution doesn't block other tasks
    counter = 0

    async def background_counter():
        nonlocal counter
        for _ in range(5):
            counter += 1
            await asyncio.sleep(0.01)

    # Concurrently feed audio and run coroutine
    source.feed_audio(b"\x00\x00" * 1024)
    await background_counter()

    assert counter == 5
    assert loop.is_running is True

    await loop.stop()
    assert loop.is_running is False


def test_tier1_hardware_capture_graceful_fallback():
    """Verify HardwareAudioCapture safely falls back without exceptions in headless CI."""
    hw_capture = HardwareAudioCapture(sample_rate=16000, chunk_size=1024)
    # On environments without hardware devices/drivers, must fall back cleanly
    assert hasattr(hw_capture, "is_hardware_available")
    assert hw_capture.is_running is False

    # feed_audio should still work via fallback without raising
    hw_capture.feed_audio(b"\x00\x00" * 1024)


def test_tier1_vad_detects_speech_vs_silence():
    """Verify VoiceActivityDetector correctly distinguishes active speech from silence."""
    vad = VoiceActivityDetector(energy_threshold=0.015, sample_rate=16000)

    # 1. Silence frame (all 0s)
    silence = generate_silence_pcm(duration_s=0.064)
    assert vad.is_speech(silence) is False
    assert vad.compute_rms(silence) == 0.0

    # 2. Speech frame (440Hz sine wave, amplitude 0.4)
    speech = generate_sine_pcm(duration_s=0.064, freq_hz=440.0, amplitude=0.4)
    assert vad.is_speech(speech) is True
    assert vad.compute_rms(speech) > 0.015


def test_tier1_vad_zero_crossing_rate():
    """Verify VAD computes zero-crossing rate reliably."""
    vad = VoiceActivityDetector(sample_rate=16000)

    # 440Hz wave has much fewer zero crossings than a 3000Hz wave
    low_freq = generate_sine_pcm(duration_s=0.064, freq_hz=440.0)
    high_freq = generate_sine_pcm(duration_s=0.064, freq_hz=3000.0)

    zcr_low = vad.compute_zcr(low_freq)
    zcr_high = vad.compute_zcr(high_freq)

    assert zcr_high > zcr_low
    metrics = vad.compute_metrics(low_freq)
    assert "rms" in metrics and "zcr" in metrics and "dbfs" in metrics


def test_tier1_utterance_segmenter_onset_confirmation():
    """Verify UtteranceSegmenter requires consecutive speech frames to confirm onset."""
    vad = VoiceActivityDetector(energy_threshold=0.015)
    segmenter = UtteranceSegmenter(vad=vad, speech_pad_chunks=2)

    speech_frame = generate_sine_pcm(duration_s=0.064, freq_hz=440.0, amplitude=0.5)
    silence_frame = generate_silence_pcm(duration_s=0.064)

    # 1st speech frame -> SPEECH_ONSET (not confirmed)
    segmenter.process_frame(speech_frame)
    assert segmenter.state == VADState.SPEECH_ONSET

    # 2nd speech frame -> Onset confirmed -> IN_SPEECH
    segmenter.process_frame(speech_frame)
    assert segmenter.state == VADState.IN_SPEECH

    # Revert test: single speech frame followed by silence drops back to SILENCE
    segmenter.reset()
    segmenter.process_frame(speech_frame)
    assert segmenter.state == VADState.SPEECH_ONSET
    segmenter.process_frame(silence_frame)
    assert segmenter.state == VADState.SILENCE


def test_tier1_utterance_segmenter_trailing_silence_and_preroll():
    """Verify UtteranceSegmenter finalizes utterance on trailing silence and retains pre-roll."""
    vad = VoiceActivityDetector(energy_threshold=0.015)
    segmenter = UtteranceSegmenter(
        vad=vad,
        silence_duration=0.32,  # 5 frames of 64ms
        preroll_duration=0.128,  # 2 frames of 64ms
        min_speech_duration=0.4,
    )

    silence = generate_silence_pcm(duration_s=0.064)
    speech = generate_sine_pcm(duration_s=0.064, freq_hz=500.0, amplitude=0.5)

    # Initial silence (fills pre-roll)
    for _ in range(3):
        segmenter.process_frame(silence)

    # 8 speech frames (~512ms > 0.4s min duration)
    for _ in range(8):
        res = segmenter.process_frame(speech)
        assert res is None

    # Feed trailing silence (5 frames required to trigger)
    result = None
    for _ in range(5):
        result = segmenter.process_frame(silence)
        if result is not None:
            break

    assert result is not None
    # Result must contain pre-roll + speech + trailing frames
    assert len(result) >= (8 * 2048)
    assert segmenter.last_utterance_info["speech_duration_s"] >= 0.4


def test_tier1_wake_word_activation_hey_serein():
    """Verify WakeWordDetector triggers activation on 'Hey Serein' audio."""
    detector = WakeWordDetector(wake_word="Hey Serein", sensitivity=0.5)
    wake_pcm = generate_wake_word_pcm(duration_s=1.152)

    # Feed chunk-by-chunk
    frame_size = 2048
    detected = False
    max_conf = 0.0

    for i in range(0, len(wake_pcm), frame_size):
        frame = wake_pcm[i : i + frame_size]
        if len(frame) == frame_size:
            res = detector.process_frame_sync(frame)
            if res.confidence > max_conf:
                max_conf = res.confidence
            if res.detected:
                detected = True
                assert res.wake_word == "Hey Serein"
                assert res.confidence >= 0.5

    assert detected is True
    assert max_conf >= 0.5


def test_tier1_wake_word_silence_suppression():
    """Verify silence suppresses wake word detection and yields 0.0 confidence."""
    detector = WakeWordDetector(wake_word="Hey Serein", sensitivity=0.5)
    silence = generate_silence_pcm(duration_s=2.0)

    for i in range(0, len(silence), 2048):
        frame = silence[i : i + 2048]
        if len(frame) == 2048:
            res = detector.process_frame_sync(frame)
            assert res.detected is False
            assert res.confidence == 0.0


@pytest.mark.asyncio
async def test_tier1_voice_engine_wake_word_event_bus_dispatch():
    """Verify VoiceEngine publishes WAKE_WORD_DETECTED and alias 'voice.wake_word'."""
    engine = VoiceEngine()
    await engine.initialize()

    wake_events: List[Event] = []
    alias_events: List[Event] = []

    event_bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, lambda e: wake_events.append(e))
    event_bus.subscribe("voice.wake_word", lambda e: alias_events.append(e))

    await engine.start()

    # Feed wake word PCM
    wake_pcm = generate_wake_word_pcm(duration_s=1.152)
    engine.feed_audio(wake_pcm)

    # Wait for processing
    for _ in range(50):
        if len(wake_events) > 0 and len(alias_events) > 0:
            break
        await asyncio.sleep(0.02)

    await engine.stop()

    assert len(wake_events) >= 1
    assert len(alias_events) >= 1
    event_data = wake_events[0].data
    assert isinstance(event_data, WakeWordEventData)
    assert event_data.wake_word == "Hey Serein"
    assert event_data.confidence >= 0.5


@pytest.mark.asyncio
async def test_tier1_voice_engine_voice_input_event_bus_dispatch():
    """Verify VoiceEngine publishes VOICE_INPUT and alias 'voice.input' upon completed utterance."""
    # Custom short silence duration for rapid test execution
    engine = VoiceEngine()
    await engine.initialize()
    engine.segmenter.silence_duration = 0.256  # 4 frames of 64ms
    engine.segmenter.silence_chunks_threshold = 4
    engine.segmenter.min_speech_duration = 0.3

    input_events: List[Event] = []
    alias_events: List[Event] = []

    event_bus.subscribe(CoreEvents.VOICE_INPUT, lambda e: input_events.append(e))
    event_bus.subscribe("voice.input", lambda e: alias_events.append(e))

    await engine.start()

    # Feed 500ms speech followed by 300ms trailing silence
    speech_pcm = generate_sine_pcm(duration_s=0.512, freq_hz=500.0, amplitude=0.5)
    silence_pcm = generate_silence_pcm(duration_s=0.384)

    engine.feed_audio(speech_pcm)
    engine.feed_audio(silence_pcm)

    for _ in range(50):
        if len(input_events) > 0 and len(alias_events) > 0:
            break
        await asyncio.sleep(0.02)

    await engine.stop()

    assert len(input_events) >= 1
    assert len(alias_events) >= 1
    utterance_data = input_events[0].data
    assert isinstance(utterance_data, UtteranceEventData)
    assert utterance_data.text != ""
    assert utterance_data.audio_duration > 0.0


# ============================================================================
# TIER 2: BOUNDARY & CORNER CASES
# ============================================================================

def test_tier2_headless_source_odd_bytes_rejected():
    """Verify odd byte counts in PCM feed are rejected with ValueError."""
    source = HeadlessAudioSource()
    with pytest.raises(ValueError, match="Malformed PCM data"):
        source.feed_audio(b"\x00" * 1023)  # Odd number of bytes


def test_tier2_headless_source_empty_bytes_ignored():
    """Verify feeding empty bytes is safely ignored."""
    source = HeadlessAudioSource()
    source.feed_audio(b"")
    assert source._residual_buffer == b""


def test_tier2_headless_source_arbitrary_chunk_slicing():
    """Verify arbitrary incoming chunk sizes are properly buffered and sliced into 2048-byte frames."""
    source = HeadlessAudioSource(chunk_size=1024)
    # Feed 1000 bytes then 1048 bytes (total 2048 bytes)
    source.feed_audio(b"\x00" * 1000)
    assert len(source._residual_buffer) == 1000
    assert source._queue.qsize() == 0

    source.feed_audio(b"\x00" * 1048)
    assert len(source._residual_buffer) == 0
    assert source._queue.qsize() == 1


@pytest.mark.asyncio
async def test_tier2_audio_capture_loop_queue_overflow_protection():
    """Verify AudioCaptureLoop bounded queue drops oldest frames and prevents unbounded memory growth."""
    source = HeadlessAudioSource(chunk_size=1024)
    loop = AudioCaptureLoop(source=source, max_queue_size=20)
    await loop.start()

    # Feed 50 frames into a queue sized at 20
    for _ in range(50):
        source.feed_audio(b"\x00\x00" * 1024)

    # Allow loop task to process
    await asyncio.sleep(0.05)

    assert loop.get_queue().qsize() <= 20
    assert loop.dropped_frames >= 20

    await loop.stop()


def test_tier2_vad_discard_short_noise_spikes():
    """Verify noise bursts shorter than min_speech_duration (<0.4s) are discarded."""
    vad = VoiceActivityDetector(energy_threshold=0.015)
    segmenter = UtteranceSegmenter(
        vad=vad,
        silence_duration=0.192,
        min_speech_duration=0.4,
    )

    # 128ms of loud noise (2 frames)
    short_burst = generate_sine_pcm(duration_s=0.128, freq_hz=1000.0, amplitude=0.8)
    silence = generate_silence_pcm(duration_s=0.384)

    segmenter.process_frame(short_burst[:2048])
    segmenter.process_frame(short_burst[2048:4096])

    # Trailing silence
    out = None
    for i in range(0, len(silence), 2048):
        f = silence[i : i + 2048]
        if len(f) == 2048:
            res = segmenter.process_frame(f)
            if res is not None:
                out = res

    # Short spike must be discarded!
    assert out is None


def test_tier2_vad_max_duration_guardrail():
    """Verify continuous speech is capped at max_speech_duration (e.g. 1.0s in test)."""
    vad = VoiceActivityDetector(energy_threshold=0.015)
    segmenter = UtteranceSegmenter(
        vad=vad,
        max_speech_duration=0.512,  # 8 frames of 64ms
        min_speech_duration=0.1,
    )

    speech_frame = generate_sine_pcm(duration_s=0.064, freq_hz=500.0, amplitude=0.5)

    cut_chunk = None
    for _ in range(12):  # Feed 12 frames without any silence
        res = segmenter.process_frame(speech_frame)
        if res is not None:
            cut_chunk = res
            break

    assert cut_chunk is not None
    assert len(cut_chunk) >= (8 * 2048)


def test_tier2_wake_word_debounce_refractory_period():
    """Verify rapid repeated wake-word inputs within debounce period trigger only once."""
    detector = WakeWordDetector(
        wake_word="Hey Serein",
        sensitivity=0.5,
        debounce_duration=1.5,
    )
    wake_pcm = generate_wake_word_pcm(duration_s=1.152)

    # 1st trigger
    res1 = detector.process_frame_sync(wake_pcm)
    assert res1.detected is True

    # 2nd immediate trigger (within 100ms)
    res2 = detector.process_frame_sync(wake_pcm)
    assert res2.detected is False
    assert res2.debounced is True


def test_tier2_wake_word_white_noise_rejection():
    """Verify loud white noise does not trigger wake word activation."""
    detector = WakeWordDetector(wake_word="Hey Serein", sensitivity=0.5)
    np.random.seed(42)
    noise_samples = np.random.normal(0, 0.2, 18432).astype(np.float32)
    noise_pcm = (np.clip(noise_samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()

    res = detector.process_frame_sync(noise_pcm)
    assert res.detected is False
    assert res.confidence < 0.5


def test_tier2_wake_word_pure_tone_rejection():
    """Verify pure tones (e.g. 440Hz and 1000Hz) do not trigger wake word activation."""
    detector = WakeWordDetector(wake_word="Hey Serein", sensitivity=0.5)

    for freq in (440.0, 1000.0, 2500.0):
        tone = generate_sine_pcm(duration_s=1.152, freq_hz=freq, amplitude=0.6)
        res = detector.process_frame_sync(tone)
        assert res.detected is False
        assert res.confidence < 0.5


def test_tier2_vad_empty_and_single_sample_frames():
    """Verify VAD handles degenerate/corrupt empty and odd-sized frames safely."""
    vad = VoiceActivityDetector()
    assert vad.is_speech(b"") is False
    assert vad.is_speech(b"\x00") is False
    assert vad.compute_rms(b"") == 0.0
    assert vad.compute_zcr(b"\x00\x00") == 0.0


# ============================================================================
# TIER 3: CROSS-FEATURE COMBINATIONS
# ============================================================================

@pytest.mark.asyncio
async def test_tier3_e2e_capture_to_vad_segmentation():
    """Verify pipeline flow from HeadlessAudioSource through AudioCaptureLoop into UtteranceSegmenter."""
    source = HeadlessAudioSource()
    loop = AudioCaptureLoop(source=source)
    vad = VoiceActivityDetector()
    segmenter = UtteranceSegmenter(
        vad=vad,
        silence_duration=0.192,
        min_speech_duration=0.2,
    )

    await loop.start()
    queue = loop.get_queue()

    # Feed speech + silence
    speech = generate_sine_pcm(duration_s=0.384, freq_hz=440.0, amplitude=0.5)
    silence = generate_silence_pcm(duration_s=0.256)
    source.feed_audio(speech + silence)

    utterance = None
    for _ in range(20):
        try:
            frame = await asyncio.wait_for(queue.get(), timeout=0.05)
            res = segmenter.process_frame(frame)
            if res is not None:
                utterance = res
                break
        except asyncio.TimeoutError:
            break

    await loop.stop()
    assert utterance is not None
    assert len(utterance) >= (6 * 2048)


@pytest.mark.asyncio
async def test_tier3_concurrent_audio_injection():
    """Verify multiple concurrent async producers feeding audio do not corrupt pipeline state."""
    engine = VoiceEngine()
    await engine.initialize()
    await engine.start()

    speech_chunk = generate_sine_pcm(duration_s=0.064, freq_hz=440.0)

    async def producer():
        for _ in range(10):
            engine.feed_audio(speech_chunk)
            await asyncio.sleep(0.005)

    # Launch 5 concurrent producers
    await asyncio.gather(*(producer() for _ in range(5)))

    assert engine.capture_loop.processed_frames > 0
    await engine.stop()


@pytest.mark.asyncio
async def test_tier3_voice_engine_lifecycle_idempotence():
    """Verify starting and stopping VoiceEngine multiple times operates cleanly without leaks."""
    engine = VoiceEngine()
    await engine.initialize()

    for _ in range(3):
        await engine.start()
        assert engine.is_running is True
        await asyncio.sleep(0.02)
        await engine.stop()
        assert engine.is_running is False


@pytest.mark.asyncio
async def test_tier3_voice_engine_status_reporting():
    """Verify get_status returns complete and accurate pipeline telemetry."""
    engine = VoiceEngine()
    await engine.initialize()
    await engine.start()

    status = await engine.get_status()
    assert status["initialized"] is True
    assert status["running"] is True
    assert status["wake_word"] == "Hey Serein"
    assert status["stt_backend"] is not None

    await engine.stop()
    status_stopped = await engine.get_status()
    assert status_stopped["running"] is False


@pytest.mark.asyncio
async def test_tier3_backward_compatible_wake_listener():
    """Verify legacy start_wake_word_listener interface works with external queue."""
    engine = VoiceEngine()
    await engine.initialize()

    ext_queue: asyncio.Queue = asyncio.Queue()
    task = await engine.start_wake_word_listener(ext_queue)

    events: List[Event] = []
    event_bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, lambda e: events.append(e))

    # Feed wake word into external queue
    wake_pcm = generate_wake_word_pcm(duration_s=1.152)
    await ext_queue.put(wake_pcm)

    for _ in range(30):
        if len(events) > 0:
            break
        await asyncio.sleep(0.02)

    await engine.stop_wake_word_listener(task)
    assert len(events) >= 1


# ============================================================================
# TIER 4: REAL-WORLD WORKLOAD SCENARIOS
# ============================================================================

@pytest.mark.asyncio
async def test_tier4_scenario_standard_user_conversation_flow():
    """
    Scenario 1: Standard User Wake & Utterance Flow
    User says "Hey Serein", then speaks "Open browser", pauses.
    Pipeline dispatches WAKE_WORD_DETECTED, then dispatches VOICE_INPUT with transcript.
    """
    engine = VoiceEngine()
    await engine.initialize()
    engine.segmenter.silence_duration = 0.256
    engine.segmenter.silence_chunks_threshold = 4
    engine.segmenter.min_speech_duration = 0.3

    received_events: List[str] = []
    event_bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, lambda e: received_events.append("WAKE"))
    event_bus.subscribe(CoreEvents.VOICE_INPUT, lambda e: received_events.append("UTTERANCE"))

    await engine.start()

    # Step 1: Wake phrase "Hey Serein"
    wake_pcm = generate_wake_word_pcm(duration_s=1.152)
    engine.feed_audio(wake_pcm)

    # Brief conversational pause (0.1s)
    engine.feed_audio(generate_silence_pcm(duration_s=0.128))

    # Step 2: Speech utterance (0.5s) + trailing silence (0.35s)
    speech_pcm = generate_sine_pcm(duration_s=0.512, freq_hz=480.0, amplitude=0.5)
    silence_pcm = generate_silence_pcm(duration_s=0.384)
    engine.feed_audio(speech_pcm)
    engine.feed_audio(silence_pcm)

    for _ in range(60):
        if "WAKE" in received_events and "UTTERANCE" in received_events:
            break
        await asyncio.sleep(0.02)

    await engine.stop()

    assert "WAKE" in received_events
    assert "UTTERANCE" in received_events
    # Wake must be detected before or with the utterance
    assert received_events.index("WAKE") <= received_events.index("UTTERANCE")


@pytest.mark.asyncio
async def test_tier4_scenario_prolonged_ambient_silence():
    """
    Scenario 2: Ambient Noise & Prolonged Silence
    3.0 seconds of ambient silence/noise fed. Zero wake triggers, zero false utterances.
    """
    engine = VoiceEngine()
    await engine.initialize()

    fired_events: List[str] = []
    event_bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, lambda e: fired_events.append("WAKE"))
    event_bus.subscribe(CoreEvents.VOICE_INPUT, lambda e: fired_events.append("VOICE"))

    await engine.start()

    # Feed 3.0s of silence
    engine.feed_audio(generate_silence_pcm(duration_s=3.0))

    await asyncio.sleep(0.15)
    await engine.stop()

    assert len(fired_events) == 0


@pytest.mark.asyncio
async def test_tier4_scenario_rapid_successive_utterances():
    """
    Scenario 3: Rapid Successive Utterances
    User speaks Utterance 1, pauses 250ms, speaks Utterance 2, pauses 250ms.
    Pipeline cleanly segments and emits 2 distinct VOICE_INPUT events.
    """
    engine = VoiceEngine()
    await engine.initialize()
    engine.segmenter.silence_duration = 0.192  # 3 frames
    engine.segmenter.silence_chunks_threshold = 3
    engine.segmenter.min_speech_duration = 0.25

    utterance_events: List[Event] = []
    event_bus.subscribe(CoreEvents.VOICE_INPUT, lambda e: utterance_events.append(e))

    await engine.start()

    # Utterance 1
    engine.feed_audio(generate_sine_pcm(duration_s=0.384, freq_hz=440.0, amplitude=0.5))
    engine.feed_audio(generate_silence_pcm(duration_s=0.256))

    # Wait for utterance 1
    for _ in range(40):
        if len(utterance_events) >= 1:
            break
        await asyncio.sleep(0.02)

    # Utterance 2
    engine.feed_audio(generate_sine_pcm(duration_s=0.384, freq_hz=600.0, amplitude=0.5))
    engine.feed_audio(generate_silence_pcm(duration_s=0.256))

    for _ in range(40):
        if len(utterance_events) >= 2:
            break
        await asyncio.sleep(0.02)

    await engine.stop()

    assert len(utterance_events) == 2
    assert all(isinstance(e.data, UtteranceEventData) for e in utterance_events)


@pytest.mark.asyncio
async def test_tier4_scenario_wake_word_embedded_vs_debounce():
    """
    Scenario 4: Wake Word Embedded in Speech vs Noise
    Exactly one wake trigger is dispatched even when audio continues immediately.
    """
    engine = VoiceEngine()
    await engine.initialize()

    wake_count = 0
    event_bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, lambda e: globals().__setitem__("wc", globals().get("wc", 0) + 1))

    globals()["wc"] = 0
    await engine.start()

    wake_pcm = generate_wake_word_pcm(duration_s=1.152)
    # Feed wake word twice rapidly within 500ms
    engine.feed_audio(wake_pcm)
    engine.feed_audio(wake_pcm)

    await asyncio.sleep(0.1)
    await engine.stop()

    assert globals()["wc"] == 1


@pytest.mark.asyncio
async def test_tier4_scenario_long_utterance_exceeding_guardrail():
    """
    Scenario 5: Long Utterance Exceeding Max Threshold
    Utterance exceeding max duration limit is cut and safely emitted.
    """
    engine = VoiceEngine()
    await engine.initialize()
    # Configure short max limit for test
    engine.segmenter.max_speech_duration = 0.512  # 8 frames of 64ms
    engine.segmenter.min_speech_duration = 0.1

    emitted_utterances: List[Event] = []
    event_bus.subscribe(CoreEvents.VOICE_INPUT, lambda e: emitted_utterances.append(e))

    await engine.start()

    # Feed 1.0s of continuous loud speech without pauses
    long_speech = generate_sine_pcm(duration_s=1.024, freq_hz=440.0, amplitude=0.5)
    engine.feed_audio(long_speech)

    for _ in range(40):
        if len(emitted_utterances) >= 1:
            break
        await asyncio.sleep(0.02)

    await engine.stop()

    assert len(emitted_utterances) >= 1
    assert emitted_utterances[0].data.audio_duration >= 0.5
