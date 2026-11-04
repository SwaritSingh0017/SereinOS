"""
Wake-Word Detection Engine for SereinOS Voice Stack.
Evaluates streaming audio frames for the wake phrase "Hey Serein".
Includes silence suppression, 1.5s refractory debounce, and pluggable backends:
- Primary/Hardware backend: openwakeword (when installed)
- Deterministic/Acoustic backend: AcousticPatternDetector (pure NumPy formant & envelope analysis)
"""
from __future__ import annotations

import collections
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger("serein.voice.wake_word")


@dataclass
class WakeWordResult:
    """Result of wake-word evaluation on an audio frame/window."""
    detected: bool = False
    wake_word: str = "Hey Serein"
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)
    debounced: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class WakeWordBackend(ABC):
    """Abstract interface for wake-word detection backends."""

    @abstractmethod
    def evaluate_window(self, samples_float32: np.ndarray, sample_rate: int) -> float:
        """Evaluate float32 PCM samples in [-1.0, 1.0]. Return confidence score in [0.0, 1.0]."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if backend is ready for inference."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def reset(self) -> None:
        pass


class OpenWakeWordBackend(WakeWordBackend):
    """Hardware / Neural backend using openWakeWord ONNX models when available."""

    def __init__(self, model_path: Optional[str] = None, wake_word: str = "Hey Serein") -> None:
        self.wake_word = wake_word
        self.model_path = model_path
        self._model: Any = None
        self._is_available = False
        self._initialize()

    def _initialize(self) -> None:
        try:
            import openwakeword  # type: ignore
            from openwakeword.model import Model  # type: ignore

            if self.model_path:
                self._model = Model(wakeword_models=[self.model_path])
            else:
                self._model = Model()
            self._is_available = True
            logger.info("OpenWakeWordBackend initialized successfully.")
        except Exception as e:
            logger.debug(f"OpenWakeWord unavailable ({e}). Using AcousticPatternDetector fallback.")
            self._is_available = False

    def is_available(self) -> bool:
        return self._is_available

    @property
    def name(self) -> str:
        return "openwakeword"

    def evaluate_window(self, samples_float32: np.ndarray, sample_rate: int) -> float:
        if not self._is_available or self._model is None:
            return 0.0
        try:
            int16_samples = (np.clip(samples_float32, -1.0, 1.0) * 32767.0).astype(np.int16)
            prediction = self._model.predict(int16_samples)
            if prediction:
                # Find matching key or maximum score
                for key, score in prediction.items():
                    if "serein" in key.lower():
                        return float(score)
                return float(max(prediction.values()))
            return 0.0
        except Exception as e:
            logger.warning(f"Error during OpenWakeWord inference: {e}")
            return 0.0

    def reset(self) -> None:
        if self._model and hasattr(self._model, "reset"):
            self._model.reset()


class AcousticPatternDetector(WakeWordBackend):
    """
    Deterministic acoustic feature and temporal envelope detector for 'Hey Serein'.
    Analyzes:
    1. Multi-band speech formant resonances:
       - F1 (300-800 Hz): "Hey" / open vowel formant
       - F2 (1200-2400 Hz): Front vowel & coronal transition
       - Sibilance (2800-4000 Hz): "S" in "Serein"
    2. Temporal envelope modulation:
       - Bimodal two-syllable envelope ("Hey" [peak] -> dip -> "Serein" [peak])
    Rejects pure tones, white noise, and ambient silence.
    """

    def __init__(self, silence_threshold: float = 0.015) -> None:
        self.silence_threshold = silence_threshold

    def is_available(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "acoustic_pattern"

    def evaluate_window(self, samples_float32: np.ndarray, sample_rate: int = 16000) -> float:
        if len(samples_float32) < 256:
            return 0.0

        # Silence Gate: evaluate RMS energy
        rms = float(np.sqrt(np.mean(np.square(samples_float32))))
        if rms < self.silence_threshold:
            return 0.0

        # 1. Spectral Analysis via FFT with Hann windowing to eliminate sidelobe leakage
        window = np.hanning(len(samples_float32))
        spec = np.abs(np.fft.rfft(samples_float32 * window))
        freqs = np.fft.rfftfreq(len(samples_float32), d=1.0 / sample_rate)
        total_energy = float(np.sum(spec)) + 1e-9

        b1_energy = float(np.sum(spec[(freqs >= 300) & (freqs <= 800)])) / total_energy
        b2_energy = float(np.sum(spec[(freqs >= 1200) & (freqs <= 2400)])) / total_energy
        b3_energy = float(np.sum(spec[(freqs >= 2800) & (freqs <= 4000)])) / total_energy

        # Speech formant presence check (must have both low and mid/high speech bands)
        formant_presence = 1.0 if (b1_energy > 0.08 and b2_energy > 0.08) else 0.0
        spectral_score = min(1.0, (b1_energy * 1.5 + b2_energy * 1.5 + b3_energy * 1.5)) * formant_presence

        # 2. Temporal Envelope Analysis (Four sub-blocks: Onset/Hey, Dip, Serein, Tail)
        quarter_blocks = np.array_split(samples_float32, 4)
        rms_quarters = [
            float(np.sqrt(np.mean(np.square(part)))) for part in quarter_blocks
        ]

        q_hey = rms_quarters[0]
        q_dip = rms_quarters[1]
        q_serein = rms_quarters[2]

        # Bimodal envelope metric: both syllables must be active above silence, and higher than the dip
        if q_hey <= self.silence_threshold or q_serein <= self.silence_threshold or min(q_hey, q_serein) <= q_dip:
            envelope_score = 0.0
        else:
            min_peak = min(q_hey, q_serein)
            envelope_score = min(1.0, max(0.0, (min_peak - q_dip) / (min_peak + 1e-6)))

        # Conjunctive confidence scoring: requires both spectral formant resonance and bimodal envelope
        confidence = spectral_score * envelope_score
        return float(np.clip(confidence, 0.0, 1.0))


class WakeWordDetector:
    """
    Streaming Wake-Word Engine for SereinOS.
    Maintains a rolling window of audio frames, performs silence suppression,
    applies sensitivity threshold, and enforces a refractory debounce period
    to prevent duplicate activations.
    """

    def __init__(
        self,
        wake_word: str = "Hey Serein",
        sensitivity: float = 0.5,
        debounce_duration: float = 1.5,
        sample_rate: int = 16000,
        chunk_size: int = 1024,
        window_duration: float = 1.152,  # ~18 frames of 64ms = 1.152s
        silence_threshold: float = 0.015,
        backend: Optional[Union[WakeWordBackend, str]] = None,
    ) -> None:
        self.wake_word = wake_word
        self.wake_phrase = wake_word
        self.sensitivity = sensitivity
        self.debounce_duration = debounce_duration
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.silence_threshold = silence_threshold

        # Frame and window sizes
        self.frame_duration_s = chunk_size / sample_rate
        self.window_frames = max(4, int(round(window_duration / self.frame_duration_s)))
        self._window_buffer: Deque[bytes] = collections.deque(maxlen=self.window_frames)

        # Debounce tracking
        self._last_trigger_time: float = -100.0
        self._running = False

        # Select backend
        if isinstance(backend, WakeWordBackend):
            self.backend = backend
        elif backend == "openwakeword":
            self.backend = OpenWakeWordBackend(wake_word=wake_word)
            if not self.backend.is_available():
                self.backend = AcousticPatternDetector(silence_threshold=silence_threshold)
        else:
            # Default: try openwakeword first; fall back to AcousticPatternDetector
            oww = OpenWakeWordBackend(wake_word=wake_word)
            if oww.is_available():
                self.backend = oww
            else:
                self.backend = AcousticPatternDetector(silence_threshold=silence_threshold)

        logger.info(
            f"WakeWordDetector initialized (wake_word='{self.wake_word}', "
            f"backend='{self.backend.name}', sensitivity={self.sensitivity}, "
            f"debounce={self.debounce_duration}s)"
        )

    async def initialize(self) -> None:
        """Asynchronously initialize detector."""
        self._running = True

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self.reset()

    def reset(self) -> None:
        """Clear rolling buffer and reset backend state."""
        self._window_buffer.clear()
        self.backend.reset()

    def process_frame_sync(self, frame_bytes: bytes) -> WakeWordResult:
        """
        Synchronously process a 1024-sample PCM frame.
        Applies silence suppression, evaluates window via backend,
        and enforces debounce refractory period.
        """
        now = time.time()
        if not frame_bytes or len(frame_bytes) < 2:
            return WakeWordResult(
                detected=False, wake_word=self.wake_word, confidence=0.0, timestamp=now
            )

        # Quick single-frame energy check for silence suppression
        samples_quick = np.frombuffer(
            frame_bytes[:len(frame_bytes) - (len(frame_bytes) % 2)], dtype=np.int16
        ).astype(np.float32) / 32768.0

        frame_rms = float(np.sqrt(np.mean(np.square(samples_quick)))) if len(samples_quick) > 0 else 0.0

        self._window_buffer.append(frame_bytes)

        # Enforce full window accumulation before running wake-word evaluation
        min_required_bytes = self.window_frames * self.chunk_size * 2
        total_window_bytes = sum(len(b) for b in self._window_buffer)
        if total_window_bytes < min_required_bytes:
            return WakeWordResult(
                detected=False, wake_word=self.wake_word, confidence=0.0, timestamp=now
            )

        # Concatenate window
        window_bytes = b"".join(self._window_buffer)
        aligned_len = len(window_bytes) - (len(window_bytes) % 2)
        window_samples = np.frombuffer(
            window_bytes[:aligned_len], dtype=np.int16
        ).astype(np.float32) / 32768.0

        # Evaluate window via active backend
        confidence = self.backend.evaluate_window(window_samples, self.sample_rate)

        # Check sensitivity threshold
        if confidence >= self.sensitivity:
            # Check refractory debounce
            time_since_last_trigger = now - self._last_trigger_time
            if time_since_last_trigger < self.debounce_duration:
                logger.debug(
                    f"Wake word detected (score={confidence:.2f}) but debounced "
                    f"({time_since_last_trigger:.2f}s < {self.debounce_duration:.2f}s)."
                )
                return WakeWordResult(
                    detected=False,
                    wake_word=self.wake_word,
                    confidence=confidence,
                    timestamp=now,
                    debounced=True,
                )

            # Trigger activation!
            self._last_trigger_time = now
            self._window_buffer.clear()
            logger.info(
                f"Wake word '{self.wake_word}' ACTIVATED (confidence={confidence:.2f})"
            )
            return WakeWordResult(
                detected=True,
                wake_word=self.wake_word,
                confidence=confidence,
                timestamp=now,
                debounced=False,
            )

        return WakeWordResult(
            detected=False,
            wake_word=self.wake_word,
            confidence=confidence,
            timestamp=now,
            debounced=False,
        )

    async def process_frame(self, frame_bytes: bytes) -> WakeWordResult:
        """Asynchronous wrapper for frame evaluation."""
        return self.process_frame_sync(frame_bytes)

    async def process_audio(self, frame_bytes: bytes) -> bool:
        """Legacy compatibility interface returning boolean activation flag."""
        res = await self.process_frame(frame_bytes)
        return res.detected


# --- Synthetic Wake-Word Audio Generator for Testing ---

def generate_wake_word_pcm(
    duration_s: float = 1.152,
    sample_rate: int = 16000,
) -> bytes:
    """
    Generate synthetic 16-bit mono Linear PCM matching the acoustic
    profile of 'Hey Serein' (two-syllable bimodal envelope with formant resonances).
    """
    num_samples = int(duration_s * sample_rate)
    samples = np.zeros(num_samples, dtype=np.float32)

    # Syllable 1: 'Hey' (0 - 260ms) -> Quarter 0
    s1_len = min(num_samples, int(0.26 * sample_rate))
    t1 = np.linspace(0, 0.26, s1_len, endpoint=False)
    env1 = np.sin(np.pi * np.linspace(0, 1, s1_len))
    samples[:s1_len] = (
        0.5 * np.sin(2.0 * np.pi * 550.0 * t1)
        + 0.35 * np.sin(2.0 * np.pi * 1800.0 * t1)
        + 0.15 * np.sin(2.0 * np.pi * 220.0 * t1)
    ) * env1

    # Dip / pause between syllables: 260ms - 550ms -> Quarter 1

    # Syllable 2: 'Serein' (550ms - 950ms) -> Quarter 2 & start of Quarter 3
    s2_start = int(0.55 * sample_rate)
    s2_len = min(num_samples - s2_start, int(0.40 * sample_rate))
    if s2_start < num_samples and s2_len > 0:
        t2 = np.linspace(0, 0.40, s2_len, endpoint=False)
        env2 = np.sin(np.pi * np.linspace(0, 1, s2_len))
        # First 80ms: sibilant 's'
        sib_len = min(s2_len, int(0.08 * sample_rate))
        samples[s2_start : s2_start + sib_len] = (
            0.4 * np.sin(2.0 * np.pi * 3200.0 * t2[:sib_len]) * env2[:sib_len]
        )
        # Remaining: voiced 'rein'
        samples[s2_start + sib_len : s2_start + s2_len] = (
            0.5 * np.sin(2.0 * np.pi * 480.0 * t2[sib_len:])
            + 0.4 * np.sin(2.0 * np.pi * 2100.0 * t2[sib_len:])
        ) * env2[sib_len:]

    pcm_int16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    return pcm_int16.tobytes()
