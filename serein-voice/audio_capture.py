"""
Audio capture subsystem for SereinOS Voice Stack.
Provides asynchronous, non-blocking audio capture loops with support for
hardware microphones (sounddevice/pyaudio with graceful fallback) and
headless mock audio feed injection for automated testing and CI.
"""
from __future__ import annotations

import asyncio
import logging
import math
import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, List, Optional, Union

import numpy as np

logger = logging.getLogger("serein.voice.audio_capture")


@dataclass
class AudioChunk:
    """Represents a discrete audio frame captured from an audio source."""
    data: bytes
    timestamp: float = field(default_factory=time.time)
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2  # 16-bit PCM = 2 bytes per sample

    @property
    def num_samples(self) -> int:
        return len(self.data) // (self.channels * self.sample_width)

    @property
    def duration_s(self) -> float:
        return self.num_samples / self.sample_rate


class AudioCaptureSource(ABC):
    """Abstract base class for audio capture sources."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.bytes_per_sample = 2  # 16-bit signed PCM
        self.frame_bytes = self.chunk_size * self.channels * self.bytes_per_sample
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    @abstractmethod
    async def start(self) -> None:
        """Initialize and start capturing audio."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop capturing and release resources."""
        pass

    @abstractmethod
    async def read_chunk(self) -> Optional[bytes]:
        """Read a single PCM chunk asynchronously. Returns None when exhausted/stopped."""
        pass

    async def stream(self) -> AsyncIterator[bytes]:
        """Asynchronously stream PCM audio chunks."""
        while self._is_running:
            chunk = await self.read_chunk()
            if chunk is None:
                if not self._is_running:
                    break
                await asyncio.sleep(0.005)
                continue
            yield chunk

    def feed_audio(self, pcm_bytes: bytes) -> None:
        """Inject raw PCM bytes into the source (headless / CI mode)."""
        raise NotImplementedError("This audio source does not support programmatic feed injection.")


class HeadlessAudioSource(AudioCaptureSource):
    """
    Headless mock audio source for CI environments and programmatic testing.
    Buffers incoming PCM bytes in a thread-safe, non-blocking queue and slices
    arbitrary input lengths into exact 1024-sample (2048-byte) frames.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
        max_queue_size: int = 500,
    ) -> None:
        super().__init__(sample_rate=sample_rate, channels=channels, chunk_size=chunk_size)
        self.max_queue_size = max_queue_size
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_queue_size)
        self._residual_buffer: bytearray = bytearray()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._is_running = True
        logger.debug("HeadlessAudioSource started.")

    async def stop(self) -> None:
        self._is_running = False
        self._residual_buffer.clear()
        logger.debug("HeadlessAudioSource stopped.")

    def feed_audio(self, pcm_bytes: bytes) -> None:
        """
        Inject PCM audio bytes into the headless source.
        Validates 16-bit sample alignment, buffers residual bytes across calls,
        and slices into standard frame_bytes chunks.
        """
        if not pcm_bytes:
            return

        # Validate 16-bit alignment (must be multiple of 2 bytes)
        if len(pcm_bytes) % 2 != 0:
            raise ValueError(
                f"Malformed PCM data: odd number of bytes ({len(pcm_bytes)}). "
                f"16-bit PCM requires 2 bytes per sample."
            )

        self._residual_buffer.extend(pcm_bytes)

        # Slice complete frames of frame_bytes
        while len(self._residual_buffer) >= self.frame_bytes:
            frame = bytes(self._residual_buffer[:self.frame_bytes])
            del self._residual_buffer[:self.frame_bytes]

            try:
                self._queue.put_nowait(frame)
            except asyncio.QueueFull:
                # Overflow policy: drop oldest frame to maintain low latency
                try:
                    self._queue.get_nowait()
                except (asyncio.QueueEmpty, ValueError):
                    pass
                try:
                    self._queue.put_nowait(frame)
                except asyncio.QueueFull:
                    pass

    async def read_chunk(self) -> Optional[bytes]:
        """Read next framed chunk from internal queue."""
        if not self._is_running and self._queue.empty():
            return None

        try:
            # Short timeout allows checking self._is_running periodically
            return await asyncio.wait_for(self._queue.get(), timeout=0.05)
        except asyncio.TimeoutError:
            return None

    def clear(self) -> None:
        """Clear all buffered frames and residual data."""
        self._residual_buffer.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except (asyncio.QueueEmpty, ValueError):
                break


class HardwareAudioCapture(AudioCaptureSource):
    """
    Physical microphone capture with dynamic driver detection (sounddevice or pyaudio).
    If audio devices or drivers are unavailable, gracefully falls back to headless mode
    with an informational log, preventing asyncio crashes.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
        device_index: Optional[int] = None,
    ) -> None:
        super().__init__(sample_rate=sample_rate, channels=channels, chunk_size=chunk_size)
        self.device_index = device_index
        self.is_hardware_available = False
        self._driver_type: Optional[str] = None
        self._stream_handle: Any = None
        self._headless_fallback = HeadlessAudioSource(
            sample_rate=sample_rate,
            channels=channels,
            chunk_size=chunk_size,
        )
        self._detect_driver()

    def _detect_driver(self) -> None:
        """Inspect environment for sounddevice or pyaudio."""
        try:
            import sounddevice as sd  # type: ignore
            # Test query devices
            sd.query_devices()
            self._driver_type = "sounddevice"
            self.is_hardware_available = True
            logger.info("Hardware audio capture detected driver: sounddevice")
            return
        except Exception as e:
            logger.debug(f"sounddevice driver unavailable: {e}")

        try:
            import pyaudio  # type: ignore
            pa = pyaudio.PyAudio()
            if pa.get_device_count() > 0:
                self._driver_type = "pyaudio"
                self.is_hardware_available = True
                logger.info("Hardware audio capture detected driver: pyaudio")
                pa.terminate()
                return
            pa.terminate()
        except Exception as e:
            logger.debug(f"pyaudio driver unavailable: {e}")

        logger.info(
            "Hardware audio capture not available (missing drivers/devices). "
            "Falling back gracefully to headless/mock mode."
        )
        self.is_hardware_available = False
        self._driver_type = "fallback_headless"

    async def start(self) -> None:
        self._is_running = True
        if not self.is_hardware_available:
            await self._headless_fallback.start()
            return

        self._loop = asyncio.get_running_loop()

        if self._driver_type == "sounddevice":
            try:
                import sounddevice as sd

                def sd_callback(indata, frames, time_info, status):
                    if status:
                        logger.warning(f"sounddevice status warning: {status}")
                    pcm = (indata * 32767).astype(np.int16).tobytes()
                    if self._loop and self._loop.is_running():
                        self._loop.call_soon_threadsafe(self._headless_fallback.feed_audio, pcm)
                    else:
                        self._headless_fallback.feed_audio(pcm)

                self._stream_handle = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="float32",
                    blocksize=self.chunk_size,
                    callback=sd_callback,
                    device=self.device_index,
                )
                self._stream_handle.start()
                logger.info("sounddevice audio capture stream started.")
            except Exception as e:
                logger.warning(
                    f"Failed to start sounddevice stream: {e}. Falling back to headless."
                )
                self.is_hardware_available = False
                await self._headless_fallback.start()

        elif self._driver_type == "pyaudio":
            try:
                import pyaudio

                pa = pyaudio.PyAudio()
                self._stream_handle = pa.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=self.sample_rate,
                    input=True,
                    frames_per_buffer=self.chunk_size,
                    input_device_index=self.device_index,
                )
                logger.info("pyaudio audio capture stream started.")
            except Exception as e:
                logger.warning(
                    f"Failed to start pyaudio stream: {e}. Falling back to headless."
                )
                self.is_hardware_available = False
                await self._headless_fallback.start()

    async def stop(self) -> None:
        self._is_running = False
        if self._stream_handle:
            try:
                if self._driver_type == "sounddevice":
                    self._stream_handle.stop()
                    self._stream_handle.close()
                elif self._driver_type == "pyaudio":
                    self._stream_handle.stop_stream()
                    self._stream_handle.close()
            except Exception as e:
                logger.debug(f"Error closing hardware audio stream: {e}")
            finally:
                self._stream_handle = None

        await self._headless_fallback.stop()
        logger.debug("HardwareAudioCapture stopped.")

    async def read_chunk(self) -> Optional[bytes]:
        if not self.is_hardware_available or self._driver_type == "fallback_headless":
            return await self._headless_fallback.read_chunk()

        if self._driver_type == "sounddevice":
            return await self._headless_fallback.read_chunk()

        if self._driver_type == "pyaudio" and self._stream_handle:
            try:
                return await asyncio.to_thread(
                    self._stream_handle.read, self.chunk_size, exception_on_overflow=False
                )
            except Exception as e:
                logger.warning(f"Error reading from pyaudio stream: {e}")
                return None

        return None

    def feed_audio(self, pcm_bytes: bytes) -> None:
        """Forward feed_audio to headless fallback."""
        self._headless_fallback.feed_audio(pcm_bytes)


class AudioCaptureLoop:
    """
    Asynchronous audio capture loop running concurrently on the asyncio event loop.
    Decouples raw audio ingestion from downstream VAD and wake-word consumers using
    a bounded FIFO queue with overflow protection.
    """

    def __init__(
        self,
        source: Optional[AudioCaptureSource] = None,
        max_queue_size: int = 100,
        on_frame: Optional[Callable[[bytes], Any]] = None,
    ) -> None:
        self.source = source or HeadlessAudioSource()
        self.max_queue_size = max_queue_size
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_queue_size)
        self._on_frame = on_frame
        self._task: Optional[asyncio.Task] = None
        self._is_running = False
        self._dropped_frames = 0
        self._processed_frames = 0

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @property
    def processed_frames(self) -> int:
        return self._processed_frames

    def get_queue(self) -> asyncio.Queue[bytes]:
        """Obtain downstream frame queue."""
        return self._queue

    def feed_audio(self, pcm_bytes: bytes) -> None:
        """Inject raw PCM into the underlying audio source."""
        self.source.feed_audio(pcm_bytes)

    async def start(self) -> asyncio.Task:
        """Start the audio capture loop in a background task."""
        if self._is_running:
            return self._task  # type: ignore

        self._is_running = True
        await self.source.start()
        self._task = asyncio.create_task(self._run_loop(), name="AudioCaptureLoopTask")
        logger.info("AudioCaptureLoop started successfully.")
        return self._task

    async def _run_loop(self) -> None:
        """Background coroutine consuming audio stream and dispatching to queue/callbacks."""
        try:
            async for frame in self.source.stream():
                if not self._is_running:
                    break

                self._processed_frames += 1

                # Handle queue full: drop oldest frame (FIFO overflow policy)
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                        self._dropped_frames += 1
                        logger.debug(
                            f"AudioCaptureLoop queue full (capacity {self.max_queue_size}). "
                            f"Dropped oldest frame. Total dropped: {self._dropped_frames}"
                        )
                    except (asyncio.QueueEmpty, ValueError):
                        pass

                try:
                    self._queue.put_nowait(frame)
                except asyncio.QueueFull:
                    self._dropped_frames += 1

                # Invoke optional frame callback
                if self._on_frame:
                    try:
                        res = self._on_frame(frame)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        logger.error(f"Error in on_frame callback: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.debug("AudioCaptureLoop task cancelled.")
        except Exception as e:
            logger.error(f"Unexpected error in AudioCaptureLoop: {e}", exc_info=True)
        finally:
            self._is_running = False

    async def stop(self) -> None:
        """Gracefully stop capture loop and release resources."""
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self.source.stop()
        logger.info("AudioCaptureLoop stopped cleanly.")


# --- Synthetic PCM Generation Helpers for Testing ---

def generate_silence_pcm(
    duration_s: float,
    sample_rate: int = 16000,
    channels: int = 1,
) -> bytes:
    """Generate 16-bit mono silence (all zeros)."""
    num_samples = int(duration_s * sample_rate * channels)
    return b"\x00\x00" * num_samples


def generate_sine_pcm(
    duration_s: float,
    freq_hz: float = 440.0,
    amplitude: float = 0.5,
    sample_rate: int = 16000,
) -> bytes:
    """Generate 16-bit mono sine wave PCM bytes."""
    num_samples = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, num_samples, endpoint=False, dtype=np.float32)
    waveform = amplitude * np.sin(2.0 * np.pi * freq_hz * t)
    # Clip and scale to int16
    pcm_int16 = (np.clip(waveform, -1.0, 1.0) * 32767.0).astype(np.int16)
    return pcm_int16.tobytes()
