"""
Voice Activity Detection (VAD) and Utterance Segmentation for SereinOS Voice Stack.
Fully compliant with Python 3.14 (PEP 594 compliant, zero audioop dependency).
Uses NumPy vector operations for RMS energy, zero-crossing rates, and adaptive noise tracking.
"""
from __future__ import annotations

import collections
import logging
import math
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("serein.voice.vad")


class VADState(str, Enum):
    """Utterance segmenter lifecycle states."""
    SILENCE = "silence"
    SPEECH_ONSET = "speech_onset"
    IN_SPEECH = "in_speech"
    SPEECH_HOLD = "speech_hold"
    UTTERANCE_COMPLETE = "utterance_complete"


class VoiceActivityDetector:
    """
    NumPy-based streaming Voice Activity Detector.
    Calculates frame RMS energy and zero-crossing rates without audioop.
    Adapts noise floor dynamically during silence periods.
    """

    def __init__(
        self,
        energy_threshold: float = 0.015,
        sample_rate: int = 16000,
        adaptation_rate: float = 0.05,
        zcr_threshold: float = 0.02,
        max_noise_floor: float = 0.04,
    ) -> None:
        self.energy_threshold = energy_threshold
        self.sample_rate = sample_rate
        self.adaptation_rate = adaptation_rate
        self.zcr_threshold = zcr_threshold
        self.max_noise_floor = max_noise_floor
        self._noise_floor = 0.005
        self._total_frames = 0
        self._speech_frames = 0
        self._history_rms: Deque[float] = collections.deque(maxlen=50)

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    def compute_rms(self, frame_bytes: bytes) -> float:
        """Calculate normalized RMS energy (0.0 to 1.0) of 16-bit PCM bytes with DC detrending."""
        if not frame_bytes or len(frame_bytes) < 2:
            return 0.0

        # Discard trailing odd byte if present for int16 alignment
        aligned_len = len(frame_bytes) - (len(frame_bytes) % 2)
        samples = np.frombuffer(frame_bytes[:aligned_len], dtype=np.int16).astype(np.float32) / 32768.0

        if len(samples) == 0:
            return 0.0

        # Detrend DC offset to prevent static bias from inflating RMS energy
        samples = samples - np.mean(samples)
        return float(np.sqrt(np.mean(np.square(samples))))

    def compute_zcr(self, frame_bytes: bytes) -> float:
        """Calculate zero-crossing rate of 16-bit PCM bytes."""
        if not frame_bytes or len(frame_bytes) < 4:
            return 0.0

        aligned_len = len(frame_bytes) - (len(frame_bytes) % 2)
        samples = np.frombuffer(frame_bytes[:aligned_len], dtype=np.int16).astype(np.float32)

        if len(samples) < 2:
            return 0.0

        signs = np.sign(samples)
        # Replace 0 with 1 to avoid false crossings at exact zero
        signs[signs == 0] = 1.0
        diffs = np.diff(signs)
        return float(np.mean(np.abs(diffs))) / 2.0

    def compute_metrics(self, frame_bytes: bytes) -> Dict[str, Any]:
        """Compute full audio metrics for diagnostics."""
        rms = self.compute_rms(frame_bytes)
        zcr = self.compute_zcr(frame_bytes)
        is_speech = self.is_speech(frame_bytes)
        dbfs = 20.0 * math.log10(rms + 1e-9)

        return {
            "rms": rms,
            "zcr": zcr,
            "is_speech": is_speech,
            "noise_floor": self._noise_floor,
            "dbfs": dbfs,
        }

    def is_speech(self, frame_bytes: bytes) -> bool:
        """
        Evaluate whether the given PCM frame contains voice activity.
        Enforces Zero-Crossing Rate (ZCR) gating to reject static DC bias and low-frequency drift.
        Adapts noise floor dynamically across continuous frames to prevent adaptation deadlock.
        """
        if not frame_bytes or len(frame_bytes) < 2:
            return False

        rms = self.compute_rms(frame_bytes)
        zcr = self.compute_zcr(frame_bytes)
        self._total_frames += 1
        self._history_rms.append(rms)

        effective_threshold = max(self.energy_threshold, self._noise_floor * 2.0)
        speech_detected = (rms >= effective_threshold) and (zcr >= self.zcr_threshold)

        if speech_detected:
            self._speech_frames += 1

        # Smooth noise floor adaptation:
        # Use rolling 10th percentile minimum statistics to prevent adaptation deadlock
        # on continuous ambient noise (e.g. HVAC, fans), capped at max_noise_floor to prevent
        # speech from being swallowed as ambient noise.
        if len(self._history_rms) >= 10:
            floor_candidate = min(
                self.max_noise_floor, float(np.percentile(self._history_rms, 10))
            )
            self._noise_floor = min(
                self.max_noise_floor,
                (1.0 - self.adaptation_rate) * self._noise_floor
                + self.adaptation_rate * floor_candidate,
            )
        elif not speech_detected or rms < self._noise_floor:
            self._noise_floor = min(
                self.max_noise_floor,
                (1.0 - self.adaptation_rate) * self._noise_floor
                + self.adaptation_rate * rms,
            )

        return speech_detected

    def reset(self) -> None:
        """Reset internal frame counters, noise floor, and RMS history."""
        self._noise_floor = 0.005
        self._total_frames = 0
        self._speech_frames = 0
        self._history_rms.clear()


class UtteranceSegmenter:
    """
    Streaming Utterance Segmenter with circular pre-roll buffer,
    onset confirmation, trailing silence segmentation, and duration guardrails.
    """

    def __init__(
        self,
        vad: Optional[VoiceActivityDetector] = None,
        sample_rate: int = 16000,
        chunk_size: int = 1024,
        silence_duration: float = 1.5,
        preroll_duration: float = 0.3,
        min_speech_duration: float = 0.4,
        max_speech_duration: float = 15.0,
        speech_pad_chunks: int = 2,
    ) -> None:
        self.vad = vad or VoiceActivityDetector(sample_rate=sample_rate)
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.frame_duration_s = chunk_size / sample_rate  # 64ms for 1024 at 16kHz
        self.silence_duration = silence_duration
        self.preroll_duration = preroll_duration
        self.min_speech_duration = min_speech_duration
        self.max_speech_duration = max_speech_duration
        self.speech_pad_chunks = speech_pad_chunks

        # Calculate chunk counts
        self.preroll_chunks = max(2, int(round(preroll_duration / self.frame_duration_s)))
        self.silence_chunks_threshold = max(
            1, int(round(silence_duration / self.frame_duration_s))
        )

        # Ring buffer preserving pre-roll audio frames
        self._preroll_buffer: Deque[bytes] = collections.deque(maxlen=self.preroll_chunks)
        self._speech_frames: List[bytes] = []
        self._onset_frames: List[bytes] = []

        self._state = VADState.SILENCE
        self._trailing_silence_count = 0
        self._last_utterance_info: Dict[str, Any] = {}

    @property
    def state(self) -> VADState:
        return self._state

    @property
    def last_utterance_info(self) -> Dict[str, Any]:
        return self._last_utterance_info

    def reset(self) -> None:
        """Reset segmenter state and clear internal frame buffers."""
        self._state = VADState.SILENCE
        self._preroll_buffer.clear()
        self._speech_frames.clear()
        self._onset_frames.clear()
        self._trailing_silence_count = 0

    def process_frame(self, frame_bytes: bytes) -> Optional[bytes]:
        """
        Process a single audio frame and advance the segmentation state machine.
        Returns complete concatenated PCM bytes if an utterance has completed,
        or None otherwise.
        """
        if not frame_bytes or len(frame_bytes) < 2:
            return None

        is_speech = self.vad.is_speech(frame_bytes)

        # State: SILENCE
        if self._state == VADState.SILENCE:
            if is_speech:
                self._state = VADState.SPEECH_ONSET
                self._onset_frames = [frame_bytes]
            else:
                self._preroll_buffer.append(frame_bytes)
            return None

        # State: SPEECH_ONSET (Confirming onset over speech_pad_chunks)
        elif self._state == VADState.SPEECH_ONSET:
            if is_speech:
                self._onset_frames.append(frame_bytes)
                if len(self._onset_frames) >= self.speech_pad_chunks:
                    # Onset confirmed! Transition to IN_SPEECH
                    self._state = VADState.IN_SPEECH
                    # Prepend pre-roll buffer to retain initial consonants/phonemes
                    self._speech_frames = list(self._preroll_buffer) + self._onset_frames
                    self._onset_frames = []
                    self._trailing_silence_count = 0
            else:
                # Transient acoustic click/noise: revert to SILENCE
                for f in self._onset_frames:
                    self._preroll_buffer.append(f)
                self._preroll_buffer.append(frame_bytes)
                self._onset_frames.clear()
                self._state = VADState.SILENCE
            return None

        # State: IN_SPEECH or SPEECH_HOLD
        elif self._state in (VADState.IN_SPEECH, VADState.SPEECH_HOLD):
            self._speech_frames.append(frame_bytes)
            current_duration = len(self._speech_frames) * self.frame_duration_s

            # Guardrail 1: Max speech duration ceiling reached -> force cut
            if current_duration >= self.max_speech_duration:
                logger.info(
                    f"Utterance reached max duration ceiling ({current_duration:.2f}s >= "
                    f"{self.max_speech_duration:.2f}s). Finalizing segment."
                )
                utterance_bytes = b"".join(self._speech_frames)
                self._record_utterance_info(utterance_bytes, current_duration, current_duration)
                self.reset()
                return utterance_bytes

            if is_speech:
                self._state = VADState.IN_SPEECH
                self._trailing_silence_count = 0
                return None
            else:
                self._state = VADState.SPEECH_HOLD
                self._trailing_silence_count += 1

                # Trailing silence trigger
                if self._trailing_silence_count >= self.silence_chunks_threshold:
                    total_duration = len(self._speech_frames) * self.frame_duration_s
                    trailing_silence_duration = (
                        self._trailing_silence_count * self.frame_duration_s
                    )
                    actual_speech_duration = max(
                        0.0, total_duration - trailing_silence_duration
                    )

                    # Guardrail 2: Reject spurious noise bursts shorter than min_speech_duration
                    if actual_speech_duration < self.min_speech_duration:
                        logger.debug(
                            f"Discarding short noise burst: speech duration "
                            f"{actual_speech_duration:.2f}s < min {self.min_speech_duration:.2f}s."
                        )
                        self.reset()
                        return None

                    # Utterance successfully completed
                    utterance_bytes = b"".join(self._speech_frames)
                    self._record_utterance_info(
                        utterance_bytes, total_duration, actual_speech_duration
                    )
                    self.reset()
                    return utterance_bytes

                return None

        return None

    def _record_utterance_info(
        self,
        utterance_bytes: bytes,
        total_duration: float,
        speech_duration: float,
    ) -> None:
        """Store metadata for completed utterance."""
        rms = self.vad.compute_rms(utterance_bytes)
        self._last_utterance_info = {
            "bytes_length": len(utterance_bytes),
            "total_duration_s": total_duration,
            "speech_duration_s": speech_duration,
            "sample_rate": self.sample_rate,
            "rms": rms,
        }
