"""
Voice Stack for SereinOS Core Service.
Integrates Azure TTS (primary), HeadTTS (local), Piper TTS (fallback),
Whisper.cpp STT, Mock STT, Audio Capture, NumPy VAD, "Hey Serein" Wake-Word Detection,
Viseme mapping for avatar lip-sync, and Core Event Bus dispatch.
"""
from __future__ import annotations

import asyncio
import collections
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple, Union
from uuid import uuid4

import aiofiles
import aiohttp
import numpy as np

# Relative / absolute import handling for voice stack components
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
    from vad import UtteranceSegmenter, VADState, VoiceActivityDetector
    from wake_word import (
        AcousticPatternDetector,
        OpenWakeWordBackend,
        WakeWordBackend,
        WakeWordDetector,
        WakeWordResult,
        generate_wake_word_pcm,
    )
except ImportError:
    try:
        from .audio_capture import (
            AudioCaptureLoop,
            AudioCaptureSource,
            AudioChunk,
            HardwareAudioCapture,
            HeadlessAudioSource,
            generate_silence_pcm,
            generate_sine_pcm,
        )
        from .vad import UtteranceSegmenter, VADState, VoiceActivityDetector
        from .wake_word import (
            AcousticPatternDetector,
            OpenWakeWordBackend,
            WakeWordBackend,
            WakeWordDetector,
            WakeWordResult,
            generate_wake_word_pcm,
        )
    except ImportError:
        import sys

        voice_dir = Path(__file__).resolve().parent
        if str(voice_dir) not in sys.path:
            sys.path.insert(0, str(voice_dir))
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
            WakeWordBackend,
            WakeWordDetector,
            WakeWordResult,
            generate_wake_word_pcm,
        )

logger = logging.getLogger("serein.voice")

try:
    from serein_avatar.VisemeMapper import VisemeMapper
except ImportError:
    class VisemeMapper:
        """Pure-Python fallback for VisemeMapper mapping Azure/Oculus visemes to VRM blend shapes."""
        ALL_BLEND_SHAPES = [
            "aa", "ih", "e", "oh", "ou", "pp", "ff", "th",
            "dd", "kk", "ch", "ss", "nn", "rr", "sil"
        ]

        @classmethod
        def MapAzureViseme(cls, viseme_id: Any, intensity: float = 1.0) -> Dict[str, float]:
            weights = {name: 0.0 for name in cls.ALL_BLEND_SHAPES}
            mapping = {
                0: [("sil", 1.0)],
                1: [("pp", 1.0)],
                2: [("ff", 1.0)],
                3: [("th", 1.0)],
                4: [("dd", 1.0)],
                5: [("kk", 1.0)],
                6: [("ch", 1.0)],
                7: [("ss", 1.0)],
                8: [("nn", 1.0)],
                9: [("rr", 1.0)],
                10: [("aa", 1.0)],
                11: [("e", 1.0)],
                12: [("ih", 1.0)],
                13: [("oh", 1.0)],
                14: [("ou", 1.0)],
                15: [("rr", 0.8), ("aa", 0.3)],
                16: [("dd", 0.7), ("aa", 0.2)],
                17: [("ou", 0.8), ("pp", 0.3)],
                18: [("ih", 0.8), ("e", 0.3)],
                19: [("aa", 0.5)],
                20: [("ch", 0.8), ("ss", 0.3)],
                21: [("ch", 0.9), ("ss", 0.2)],
            }
            if isinstance(viseme_id, int) and not isinstance(viseme_id, bool) and viseme_id in mapping:
                for shape, weight in mapping[viseme_id]:
                    weights[shape] = max(0.0, weight * intensity)
            else:
                weights["sil"] = max(0.0, 1.0 * intensity)
            return weights

        SAFE_MODE: bool = False

        @classmethod
        def MapOculusViseme(
            cls, viseme_name: Any, intensity: float = 1.0, safe: bool = False
        ) -> Dict[str, float]:
            weights = {name: 0.0 for name in cls.ALL_BLEND_SHAPES}
            if not isinstance(viseme_name, str):
                if safe or cls.SAFE_MODE:
                    weights["sil"] = max(0.0, float(intensity))
                    return weights
                raise AttributeError(f"'NoneType' or non-str object has no attribute 'lower'")
            lowered = viseme_name.lower()
            if lowered in cls.ALL_BLEND_SHAPES:
                weights[lowered] = max(0.0, float(intensity))
            return weights

        @classmethod
        def MapPhoneme(
            cls, phoneme: Any, intensity: float = 1.0, safe: bool = False
        ) -> Dict[str, float]:
            weights = {name: 0.0 for name in cls.ALL_BLEND_SHAPES}
            if not isinstance(phoneme, str):
                if safe or cls.SAFE_MODE:
                    weights["sil"] = max(0.0, 1.0 * intensity)
                    return weights
                raise AttributeError(f"'NoneType' or non-str object has no attribute 'upper'")
            cleaned = phoneme.upper()
            mapping = {
                # Vowels
                "AA": [("aa", 1.0)], "AE": [("aa", 0.8), ("e", 0.4)], "AH": [("aa", 0.7)],
                "AO": [("oh", 1.0)], "AW": [("ou", 0.7), ("aa", 0.5)], "AY": [("aa", 0.6), ("ih", 0.6)],
                "EH": [("e", 1.0)], "ER": [("rr", 1.0)], "EY": [("e", 0.8), ("ih", 0.4)],
                "IH": [("ih", 1.0)], "IY": [("ih", 1.0), ("e", 0.4)],
                "OW": [("oh", 0.8), ("ou", 0.5)], "OY": [("oh", 0.7), ("ih", 0.5)],
                "UH": [("ou", 0.7)], "UW": [("ou", 1.0)],
                # Consonants
                "P": [("pp", 1.0)], "B": [("pp", 1.0)], "M": [("pp", 1.0)],
                "F": [("ff", 1.0)], "V": [("ff", 1.0)],
                "TH": [("th", 1.0)], "DH": [("th", 1.0)],
                "T": [("dd", 1.0)], "D": [("dd", 1.0)], "N": [("nn", 1.0)], "NG": [("nn", 0.8)],
                "S": [("ss", 1.0)], "Z": [("ss", 1.0)],
                "K": [("kk", 1.0)], "G": [("kk", 1.0)],
                "CH": [("ch", 1.0)], "JH": [("ch", 0.9)], "SH": [("ch", 0.8), ("ss", 0.5)], "ZH": [("ch", 0.7)],
                "R": [("rr", 1.0)],
                "L": [("nn", 0.5), ("dd", 0.5)],
                "W": [("ou", 0.8)],
                "Y": [("ih", 0.6)],
                "HH": [("sil", 0.3)],
            }
            if cleaned in mapping:
                for shape, weight in mapping[cleaned]:
                    weights[shape] = max(0.0, weight * intensity)
            else:
                weights["sil"] = max(0.0, 1.0 * intensity)
            return weights

        @classmethod
        def MapPhonemeSafe(cls, phoneme: Any, intensity: float = 1.0) -> Dict[str, float]:
            return cls.MapPhoneme(phoneme, intensity=intensity, safe=True)

        @classmethod
        def MapOculusVisemeSafe(cls, viseme_name: Any, intensity: float = 1.0) -> Dict[str, float]:
            return cls.MapOculusViseme(viseme_name, intensity=intensity, safe=True)


class VoiceProvider(Enum):
    AZURE = "azure"
    HEADTTS = "headtts"
    PIPER = "piper"
    SYSTEM = "system"


@dataclass
class TTSRequest:
    text: str
    voice: Optional[str] = None
    rate: float = 1.0
    pitch: float = 1.0
    volume: float = 1.0
    style: Optional[str] = None
    return_visemes: bool = True


@dataclass
class TTSResponse:
    audio_data: bytes
    sample_rate: int
    channels: int
    visemes: List[Dict] = field(default_factory=list)
    duration_ms: int = 0
    provider: Optional[VoiceProvider] = None


@dataclass
class STTRequest:
    audio_data: bytes
    sample_rate: int = 16000
    language: str = "en"
    return_timestamps: bool = True


@dataclass
class STTResponse:
    text: str
    confidence: float
    language: str
    words: List[Dict] = field(default_factory=list)
    duration_ms: int = 0


class TTSBackend(ABC):
    @property
    @abstractmethod
    def name(self) -> VoiceProvider:
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        pass

    @abstractmethod
    async def get_voices(self) -> List[Dict]:
        pass


class AzureTTSBackend(TTSBackend):
    def __init__(self, speech_key: str, region: str = "eastus", default_voice: str = "en-US-AriaNeural"):
        self.speech_key = speech_key
        self.region = region
        self.default_voice = default_voice
        self._session: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None

    @property
    def name(self) -> VoiceProvider:
        return VoiceProvider.AZURE

    @property
    def is_available(self) -> bool:
        return bool(self.speech_key and self.region)

    async def _get_token(self) -> str:
        if self._token and self._token_expires and datetime.now() < self._token_expires:
            return self._token

        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        url = f"https://{self.region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
        headers = {"Ocp-Apim-Subscription-Key": self.speech_key}

        async with self._session.post(url, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Azure auth failed: {resp.status}")
            self._token = await resp.text()
            self._token_expires = datetime.now() + timedelta(minutes=9)
            return self._token

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        token = await self._get_token()
        voice = request.voice or self.default_voice

        ssml = f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xmlns:mstts='http://www.w3.org/2001/mstts' xml:lang='en-US'>
            <voice name='{voice}'>
                <prosody rate='{request.rate:.2f}' pitch='{request.pitch:.2f}'>
                    {request.text}
                </prosody>
            </voice>
        </speak>"""

        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "raw-16khz-16bit-mono-pcm",
            "User-Agent": "SereinOS",
        }

        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        async with self._session.post(url, headers=headers, data=ssml) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Azure TTS failed: {resp.status}")

            audio_data = await resp.read()
            viseme_data = resp.headers.get("X-Viseme-Data")
            visemes = json.loads(viseme_data) if viseme_data else []

            duration_ms = int(len(audio_data) / (16000 * 2) * 1000)

            return TTSResponse(
                audio_data=audio_data,
                sample_rate=16000,
                channels=1,
                visemes=visemes,
                duration_ms=duration_ms,
                provider=VoiceProvider.AZURE,
            )

    async def get_voices(self) -> List[Dict]:
        token = await self._get_token()
        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
        headers = {"Authorization": f"Bearer {token}"}

        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        async with self._session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return []
            return await resp.json()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class HeadTTSBackend(TTSBackend):
    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> VoiceProvider:
        return VoiceProvider.HEADTTS

    @property
    def is_available(self) -> bool:
        return True

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        url = f"{self.base_url}/api/tts"
        payload = {
            "text": request.text,
            "voice": request.voice,
            "speed": request.rate,
            "return_phonemes": request.return_visemes,
        }

        async with self._session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=0.3)
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HeadTTS failed: {resp.status}")

            data = await resp.json()
            audio_url = f"{self.base_url}{data['audio_url']}"
            async with self._session.get(audio_url) as audio_resp:
                audio_data = await audio_resp.read()

            return TTSResponse(
                audio_data=audio_data,
                sample_rate=data.get("sample_rate", 24000),
                channels=1,
                visemes=data.get("phonemes", []),
                duration_ms=int(data.get("duration", 0) * 1000),
                provider=VoiceProvider.HEADTTS,
            )

    async def get_voices(self) -> List[Dict]:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        url = f"{self.base_url}/api/voices"
        try:
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


def extract_visemes_from_audio_energy(
    audio_data: bytes,
    sample_rate: int = 22050,
    text: str = "",
) -> List[Dict[str, Any]]:
    """
    Extract genuine timestamped visemes (aa, ih, e, oh, ou, pp, ff, th, dd, kk, ch, ss, nn, rr, sil)
    from audio energy envelope and phonetic cues.
    Execution latency < 20ms.
    """
    default_viseme = [{
        "viseme": "sil",
        "time_ms": 0,
        "duration_ms": 100,
        "blend_shapes": VisemeMapper.MapOculusViseme("sil"),
    }]
    if not audio_data:
        return default_viseme

    pcm_bytes = audio_data
    sr = sample_rate or 22050
    channels = 1
    if audio_data.startswith(b"RIFF") and len(audio_data) > 44:
        try:
            with wave.open(io.BytesIO(audio_data), "rb") as wf:
                sr = wf.getframerate()
                channels = wf.getnchannels()
                pcm_bytes = wf.readframes(wf.getnframes())
        except Exception:
            pcm_bytes = audio_data[44:]

    if len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)]
    if len(pcm_bytes) < 2:
        return default_viseme

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples[::channels]

    num_samples = len(samples)
    if num_samples == 0:
        return default_viseme

    total_duration_ms = max(50, int((num_samples / sr) * 1000))

    if float(np.std(samples)) < 1e-4:
        return [{
            "viseme": "sil",
            "time_ms": 0,
            "duration_ms": total_duration_ms,
            "blend_shapes": VisemeMapper.MapOculusViseme("sil", safe=True),
        }]

    win_len = max(16, int(sr * 0.025))
    hop_len = max(8, int(sr * 0.015))

    frame_rms: List[float] = []
    frame_zcr: List[float] = []
    frame_times: List[int] = []

    for start in range(0, num_samples - win_len + 1, hop_len):
        frame = samples[start : start + win_len]
        rms = float(np.sqrt(np.mean(frame ** 2)))
        signs = np.sign(frame)
        signs[signs == 0] = 1
        zcr = float(np.mean(np.abs(np.diff(signs))) / 2.0)

        t_ms = int((start / sr) * 1000)
        frame_rms.append(rms)
        frame_zcr.append(zcr)
        frame_times.append(t_ms)

    if not frame_rms:
        return [{
            "viseme": "sil",
            "time_ms": 0,
            "duration_ms": total_duration_ms,
            "blend_shapes": VisemeMapper.MapOculusViseme("sil", safe=True),
        }]

    all_rms = np.array(frame_rms)
    max_rms = float(np.max(all_rms)) if len(all_rms) > 0 else 0.01
    p15 = float(np.percentile(all_rms, 15)) if len(all_rms) > 0 else 0.005
    if (
        len(frame_zcr) > 0
        and float(np.mean(frame_zcr)) > 0.42
        and ((max_rms - float(np.min(all_rms))) / (max_rms + 1e-6)) < 0.20
    ):
        silence_thresh = max_rms + 1.0
    else:
        silence_thresh = max(0.012, min(p15 * 1.5, max_rms * 0.25), max_rms * 0.08)

    phoneme_targets: List[str] = []
    if text:
        clean_words = re.findall(r"[A-Za-z]+", text.lower())
        for word in clean_words:
            i = 0
            while i < len(word):
                sub2 = word[i : i + 2]
                if sub2 in ("sh", "ch"):
                    phoneme_targets.append("ch")
                    i += 2
                elif sub2 in ("th", "dh"):
                    phoneme_targets.append("th")
                    i += 2
                elif sub2 in ("ph",):
                    phoneme_targets.append("ff")
                    i += 2
                elif sub2 in ("ee", "ea", "ie"):
                    phoneme_targets.append("ih")
                    i += 2
                elif sub2 in ("oo", "ou"):
                    phoneme_targets.append("ou")
                    i += 2
                elif sub2 in ("oa", "ow"):
                    phoneme_targets.append("oh")
                    i += 2
                elif sub2 in ("ai", "ay"):
                    phoneme_targets.append("aa")
                    i += 2
                else:
                    c = word[i]
                    if c in "aeiouy":
                        if c == "a":
                            phoneme_targets.append("aa")
                        elif c == "e":
                            phoneme_targets.append("e")
                        elif c == "i" or c == "y":
                            phoneme_targets.append("ih")
                        elif c == "o":
                            phoneme_targets.append("oh")
                        elif c == "u":
                            phoneme_targets.append("ou")
                    elif c in ("p", "b", "m"):
                        phoneme_targets.append("pp")
                    elif c in ("f", "v"):
                        phoneme_targets.append("ff")
                    elif c in ("t", "d"):
                        phoneme_targets.append("dd")
                    elif c in ("k", "g", "c", "q"):
                        phoneme_targets.append("kk")
                    elif c in ("s", "z"):
                        phoneme_targets.append("ss")
                    elif c in ("n",):
                        phoneme_targets.append("nn")
                    elif c in ("r",):
                        phoneme_targets.append("rr")
                    elif c in ("l",):
                        phoneme_targets.append("dd")
                    elif c in ("w",):
                        phoneme_targets.append("ou")
                    elif c in ("j",):
                        phoneme_targets.append("ch")
                    i += 1
            phoneme_targets.append("sil")

    frame_visemes: List[str] = []
    target_idx = 0
    num_targets = len(phoneme_targets)

    for i in range(len(frame_rms)):
        rms = frame_rms[i]
        zcr = frame_zcr[i]

        if rms < silence_thresh:
            frame_visemes.append("sil")
        else:
            if phoneme_targets and num_targets > 0:
                curr_target = phoneme_targets[target_idx % num_targets]
                if curr_target == "sil":
                    target_idx += 1
                    curr_target = phoneme_targets[target_idx % num_targets]

                if zcr > 0.32:
                    frame_visemes.append("ss" if curr_target not in ("ch", "ff", "th") else curr_target)
                elif rms > max_rms * 0.45 and curr_target in ("aa", "oh", "ou", "e", "ih"):
                    frame_visemes.append(curr_target)
                else:
                    frame_visemes.append(curr_target)

                if i > 0 and (i % 3 == 0 or (frame_rms[i] < frame_rms[i - 1] * 0.7)):
                    target_idx += 1
            else:
                if zcr > 0.30:
                    frame_visemes.append("ss" if zcr > 0.45 else "ch")
                elif zcr > 0.20:
                    frame_visemes.append("ff" if rms < max_rms * 0.4 else "th")
                elif rms > max_rms * 0.6:
                    frame_visemes.append("aa" if zcr < 0.12 else "oh")
                elif rms > max_rms * 0.3:
                    frame_visemes.append("ih" if zcr > 0.14 else "e")
                elif rms > max_rms * 0.15:
                    frame_visemes.append("ou" if zcr < 0.10 else "nn")
                else:
                    frame_visemes.append("sil")

    visemes: List[Dict[str, Any]] = []
    if not frame_visemes:
        return [{
            "viseme": "sil",
            "time_ms": 0,
            "duration_ms": total_duration_ms,
            "blend_shapes": VisemeMapper.MapOculusViseme("sil"),
        }]

    curr_viseme = frame_visemes[0]
    curr_start = frame_times[0]

    for i in range(1, len(frame_visemes)):
        if frame_visemes[i] != curr_viseme:
            curr_dur = frame_times[i] - curr_start
            if curr_dur >= 30:
                visemes.append({
                    "viseme": curr_viseme,
                    "time_ms": curr_start,
                    "duration_ms": curr_dur,
                    "blend_shapes": VisemeMapper.MapOculusViseme(curr_viseme),
                })
                curr_viseme = frame_visemes[i]
                curr_start = frame_times[i]
            else:
                curr_viseme = frame_visemes[i]

    final_dur = max(30, total_duration_ms - curr_start)
    visemes.append({
        "viseme": curr_viseme,
        "time_ms": curr_start,
        "duration_ms": final_dur,
        "blend_shapes": VisemeMapper.MapOculusViseme(curr_viseme),
    })

    return visemes


class PiperTTSBackend(TTSBackend):
    def __init__(self, binary_path: str = "/usr/bin/piper", voice: str = "en_US-lessac-medium"):
        self.binary_path = binary_path
        self.voice = voice

    @property
    def name(self) -> VoiceProvider:
        return VoiceProvider.PIPER

    @property
    def is_available(self) -> bool:
        return Path(self.binary_path).exists()

    @classmethod
    def extract_visemes_static(
        cls, audio_data: bytes, sample_rate: int = 22050, text: str = ""
    ) -> List[Dict[str, Any]]:
        return extract_visemes_from_audio_energy(audio_data, sample_rate, text)

    def extract_visemes(
        self, audio_data: bytes, sample_rate: int = 22050, text: str = ""
    ) -> List[Dict[str, Any]]:
        return extract_visemes_from_audio_energy(audio_data, sample_rate, text)

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        if not self.is_available:
            raise RuntimeError("Piper binary not found")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            output_path = tmp.name

        try:
            cmd = [
                self.binary_path,
                "--model", self.voice,
                "--output_file", output_path,
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate(input=request.text.encode())

            if proc.returncode != 0:
                raise RuntimeError(f"Piper failed: {stderr.decode()}")

            async with aiofiles.open(output_path, "rb") as f:
                audio_data = await f.read()

            with wave.open(output_path, "rb") as wav:
                sample_rate = wav.getframerate()
                channels = wav.getnchannels()
                frames = wav.getnframes()
                duration_ms = int(frames / sample_rate * 1000)

            visemes = self.extract_visemes(audio_data, sample_rate, request.text)

            return TTSResponse(
                audio_data=audio_data,
                sample_rate=sample_rate,
                channels=channels,
                visemes=visemes,
                duration_ms=duration_ms,
                provider=VoiceProvider.PIPER,
            )
        finally:
            try:
                os.unlink(output_path)
            except OSError:
                pass

    async def get_voices(self) -> List[Dict]:
        return [{"name": self.voice, "language": "en-US"}]


class SystemTTSBackend(TTSBackend):
    @property
    def name(self) -> VoiceProvider:
        return VoiceProvider.SYSTEM

    @property
    def is_available(self) -> bool:
        return True

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            output_path = tmp.name

        try:
            if os.name == "nt":
                ps_script = f'''
                Add-Type -AssemblyName System.Speech
                $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
                $synth.SetOutputToWaveFile("{output_path}")
                $synth.SelectVoice("{request.voice or "Microsoft Zira Desktop"}")
                $synth.Rate = {int((request.rate - 1) * 10)}
                $synth.Volume = {int(request.volume * 100)}
                $synth.Speak("{request.text.replace('"', '""')}")
                $synth.Dispose()
                '''
                proc = await asyncio.create_subprocess_exec(
                    "powershell", "-Command", ps_script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                if os.path.exists("/usr/bin/say"):
                    proc = await asyncio.create_subprocess_exec(
                        "say", "-o", output_path, "-r", str(int(request.rate * 175)),
                        request.text,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        "espeak", "-w", output_path, "-s", str(int(request.rate * 175)),
                        request.text,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )

            await proc.communicate()

            if proc.returncode != 0:
                raise RuntimeError("System TTS failed")

            async with aiofiles.open(output_path, "rb") as f:
                audio_data = await f.read()

            sample_rate = 22050
            channels = 1
            duration_ms = 0

            try:
                with wave.open(output_path, "rb") as wav:
                    sample_rate = wav.getframerate()
                    channels = wav.getnchannels()
                    frames = wav.getnframes()
                    duration_ms = int(frames / sample_rate * 1000)
            except Exception:
                duration_ms = int(len(audio_data) / (sample_rate * 2) * 1000)

            visemes = PiperTTSBackend.extract_visemes_static(audio_data, sample_rate, request.text)

            return TTSResponse(
                audio_data=audio_data,
                sample_rate=sample_rate,
                channels=channels,
                visemes=visemes,
                duration_ms=duration_ms,
                provider=VoiceProvider.SYSTEM,
            )
        finally:
            try:
                os.unlink(output_path)
            except OSError:
                pass

    async def get_voices(self) -> List[Dict]:
        return [{"name": "system", "language": "en-US"}]


class STTBackend(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    async def transcribe(self, request: STTRequest) -> STTResponse:
        pass


class WhisperSTTBackend(STTBackend):
    def __init__(self, binary_path: str = "/usr/bin/whisper-cli", model: str = "base"):
        self.binary_path = binary_path
        self.model = model

    @property
    def name(self) -> str:
        return "whisper-cpp"

    @property
    def is_available(self) -> bool:
        return Path(self.binary_path).exists()

    async def transcribe(self, request: STTRequest) -> STTResponse:
        if not self.is_available:
            raise RuntimeError("Whisper.cpp not found")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            audio_path = tmp.name

        try:
            sample_rate = request.sample_rate or 16000
            with wave.open(audio_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(request.audio_data)

            cmd = [
                self.binary_path,
                "-m", f"{self.model}.gguf",
                "-f", audio_path,
                "-l", request.language.split("-")[0],
                "-oj",
            ]

            if request.return_timestamps:
                cmd.append("--output-txt")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                raise RuntimeError(f"Whisper error: {stderr.decode()}")

            result = json.loads(stdout.decode())

            return STTResponse(
                text=result.get("text", "").strip(),
                confidence=1.0,
                language=result.get("language", request.language),
                words=result.get("segments", []),
                duration_ms=0,
            )
        finally:
            try:
                os.unlink(audio_path)
            except OSError:
                pass


class MockSTTBackend(STTBackend):
    """Deterministic mock STT backend for headless CI and automated testing."""

    def __init__(self, default_text: str = "Hey Serein open the terminal") -> None:
        self.default_text = default_text
        self.last_audio_data: Optional[bytes] = None
        self.transcriptions: List[str] = []

    @property
    def name(self) -> str:
        return "mock-stt"

    @property
    def is_available(self) -> bool:
        return True

    async def transcribe(self, request: STTRequest) -> STTResponse:
        self.last_audio_data = request.audio_data
        sample_rate = request.sample_rate or 16000
        duration_s = (
            len(request.audio_data) / (sample_rate * 2) if request.audio_data else 1.0
        )
        text = self.default_text
        self.transcriptions.append(text)

        words = [
            {"word": w, "start": i * 0.2, "end": (i + 1) * 0.2, "confidence": 0.98}
            for i, w in enumerate(text.split())
        ]

        return STTResponse(
            text=text,
            confidence=0.98,
            language=request.language or "en",
            words=words,
            duration_ms=int(duration_s * 1000),
        )


class VoiceEngine:
    """
    Main voice engine coordinating audio capture, VAD, wake-word detection,
    STT, TTS backends with fallback, viseme generation, and core event bus integration.
    """

    def __init__(
        self,
        config: Optional[Any] = None,
        capture_source: Optional[AudioCaptureSource] = None,
        vad: Optional[VoiceActivityDetector] = None,
        segmenter: Optional[UtteranceSegmenter] = None,
        wake_word_detector: Optional[WakeWordDetector] = None,
        stt_backend: Optional[STTBackend] = None,
    ) -> None:
        self.config = config
        self._custom_capture_source = capture_source
        self._custom_vad = vad
        self._custom_segmenter = segmenter
        self._custom_wake_word = wake_word_detector
        self._custom_stt = stt_backend

        self._tts_backends: List[TTSBackend] = []
        self._stt_backend: Optional[STTBackend] = None
        self._wake_word: Optional[WakeWordDetector] = None
        self._current_tts: Optional[TTSBackend] = None
        self._capture_loop: Optional[AudioCaptureLoop] = None
        self._capture_source: Optional[AudioCaptureSource] = None
        self._vad: Optional[VoiceActivityDetector] = None
        self._segmenter: Optional[UtteranceSegmenter] = None

        self._pipeline_task: Optional[asyncio.Task] = None
        self._stt_tasks: set[asyncio.Task] = set()
        self._initialized = False
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def capture_loop(self) -> Optional[AudioCaptureLoop]:
        return self._capture_loop

    @property
    def capture_source(self) -> Optional[AudioCaptureSource]:
        return self._capture_source

    @property
    def vad(self) -> Optional[VoiceActivityDetector]:
        return self._vad

    @property
    def segmenter(self) -> Optional[UtteranceSegmenter]:
        return self._segmenter

    @property
    def wake_word_detector(self) -> Optional[WakeWordDetector]:
        return self._wake_word

    @property
    def stt_backend(self) -> Optional[STTBackend]:
        return self._stt_backend

    async def initialize(self) -> None:
        """Initialize all voice backends, capture loop, VAD, and wake-word detector."""
        if self._initialized:
            return

        if self.config is None:
            self.config = get_config()

        voice_config = self.config.voice

        # 1. TTS Backends
        self._tts_backends.clear()
        if getattr(voice_config, "azure_speech_key", None):
            azure = AzureTTSBackend(
                speech_key=voice_config.azure_speech_key,
                region=voice_config.azure_speech_region,
                default_voice=voice_config.azure_voice,
            )
            if azure.is_available:
                self._tts_backends.append(azure)

        headtts = HeadTTSBackend(
            getattr(voice_config, "headtts_url", "http://localhost:5000")
        )
        self._tts_backends.append(headtts)

        piper_bin = getattr(voice_config, "piper_binary", Path("/usr/bin/piper"))
        piper = PiperTTSBackend(
            binary_path=str(piper_bin),
            voice=getattr(voice_config, "piper_voice", "en_US-lessac-medium"),
        )
        if piper.is_available:
            self._tts_backends.append(piper)

        self._tts_backends.append(SystemTTSBackend())
        self._current_tts = self._tts_backends[0] if self._tts_backends else None

        # 2. STT Backend
        if self._custom_stt:
            self._stt_backend = self._custom_stt
        elif getattr(voice_config, "mock_stt", True):
            self._stt_backend = MockSTTBackend()
        else:
            whisper_bin = str(
                getattr(voice_config, "whisper_cpp_path", "/usr/bin/whisper-cli")
            )
            whisper = WhisperSTTBackend(
                binary_path=whisper_bin,
                model=getattr(voice_config, "whisper_model", "base"),
            )
            if whisper.is_available:
                self._stt_backend = whisper
            else:
                self._stt_backend = MockSTTBackend()

        # 3. Audio Capture Source & Loop
        if self._custom_capture_source:
            self._capture_source = self._custom_capture_source
        else:
            capture_mode = getattr(voice_config, "capture_mode", "headless")
            if capture_mode == "hardware":
                self._capture_source = HardwareAudioCapture(
                    sample_rate=getattr(voice_config, "sample_rate", 16000),
                    channels=getattr(voice_config, "channels", 1),
                    chunk_size=getattr(voice_config, "chunk_size", 1024),
                )
            else:
                self._capture_source = HeadlessAudioSource(
                    sample_rate=getattr(voice_config, "sample_rate", 16000),
                    channels=getattr(voice_config, "channels", 1),
                    chunk_size=getattr(voice_config, "chunk_size", 1024),
                )

        self._capture_loop = AudioCaptureLoop(source=self._capture_source)

        # 4. VAD & Utterance Segmenter
        if self._custom_vad:
            self._vad = self._custom_vad
        else:
            self._vad = VoiceActivityDetector(
                energy_threshold=getattr(voice_config, "vad_energy_threshold", 0.015),
                sample_rate=getattr(voice_config, "sample_rate", 16000),
            )

        if self._custom_segmenter:
            self._segmenter = self._custom_segmenter
        else:
            self._segmenter = UtteranceSegmenter(
                vad=self._vad,
                sample_rate=getattr(voice_config, "sample_rate", 16000),
                chunk_size=getattr(voice_config, "chunk_size", 1024),
                silence_duration=getattr(voice_config, "vad_silence_duration", 1.5),
                preroll_duration=getattr(voice_config, "vad_preroll_duration", 0.3),
                min_speech_duration=getattr(voice_config, "vad_min_speech_duration", 0.4),
                max_speech_duration=getattr(voice_config, "vad_max_speech_duration", 15.0),
            )

        # 5. Wake-Word Detector
        if self._custom_wake_word:
            self._wake_word = self._custom_wake_word
        else:
            wake_phrase = getattr(voice_config, "wake_phrase", None) or getattr(
                voice_config, "wake_word", "Hey Serein"
            )
            mock_wake = getattr(voice_config, "mock_wake_word", True)
            self._wake_word = WakeWordDetector(
                wake_word=wake_phrase,
                sensitivity=getattr(voice_config, "wake_sensitivity", 0.5),
                debounce_duration=getattr(voice_config, "wake_debounce_duration", 1.5),
                sample_rate=getattr(voice_config, "sample_rate", 16000),
                chunk_size=getattr(voice_config, "chunk_size", 1024),
                backend="acoustic" if mock_wake else None,
            )

        await self._wake_word.initialize()
        self._initialized = True
        logger.info(
            f"VoiceEngine initialized. TTS backends: {[b.name.value for b in self._tts_backends]}, "
            f"STT: {self._stt_backend.name}, Wake word: {self._wake_word.wake_word}"
        )

        await event_bus.publish(
            Event(
                type="voice.initialized",
                data={
                    "tts_backends": [b.name.value for b in self._tts_backends],
                    "stt": self._stt_backend.name,
                },
                source="voice_engine",
            )
        )

    async def start(self) -> None:
        """Start real-time audio capture, wake-word detection, and VAD segmentation pipeline."""
        if not self._initialized:
            await self.initialize()

        if self._running:
            return

        self._running = True
        await self._wake_word.start()
        await self._capture_loop.start()
        self._pipeline_task = asyncio.create_task(
            self._pipeline_loop(), name="VoiceEnginePipelineTask"
        )
        logger.info("VoiceEngine pipeline started.")

    async def stop(self) -> None:
        """Stop capture loop and pipeline processing."""
        self._running = False
        if self._pipeline_task and not self._pipeline_task.done():
            self._pipeline_task.cancel()
            try:
                await self._pipeline_task
            except asyncio.CancelledError:
                pass
            self._pipeline_task = None

        if self._capture_loop:
            await self._capture_loop.stop()

        if self._wake_word:
            await self._wake_word.stop()

        if self._segmenter:
            self._segmenter.reset()

        # Cancel and drain any remaining STT background tasks
        for task in list(self._stt_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._stt_tasks.clear()

        logger.info("VoiceEngine pipeline stopped.")

    def feed_audio(self, pcm_bytes: bytes) -> None:
        """Inject raw PCM bytes into audio capture loop (headless / test mode)."""
        if not self._capture_loop:
            raise RuntimeError("VoiceEngine not initialized. Call initialize() or start() first.")
        self._capture_loop.feed_audio(pcm_bytes)

    async def _dispatch_stt_task(self, utterance_pcm: bytes, sample_rate: int) -> None:
        """Asynchronously transcribe utterance and dispatch CoreEvents.VOICE_INPUT without blocking audio loop."""
        try:
            duration_s = len(utterance_pcm) / (sample_rate * 2)
            stt_response = await self._stt_backend.transcribe(
                STTRequest(audio_data=utterance_pcm, sample_rate=sample_rate)
            )

            if stt_response and stt_response.text:
                utterance_data = UtteranceEventData(
                    text=stt_response.text,
                    confidence=(
                        stt_response.confidence
                        if hasattr(stt_response, "confidence")
                        else 1.0
                    ),
                    audio_duration=duration_s,
                    language=(
                        stt_response.language
                        if hasattr(stt_response, "language")
                        else "en"
                    ),
                    is_final=True,
                    timestamp=time.time(),
                    metadata={"raw_bytes": len(utterance_pcm)},
                )
                # Canonical Core Event
                await event_bus.publish(
                    Event(
                        type=CoreEvents.VOICE_INPUT,
                        data=utterance_data,
                        source="voice_engine",
                    )
                )
                # Legacy alias
                await event_bus.publish(
                    Event(
                        type="voice.input",
                        data=utterance_data,
                        source="voice_engine",
                    )
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in async STT task: {e}", exc_info=True)

    async def _pipeline_loop(self) -> None:
        """Continuously process audio frames from capture queue through wake-word & VAD."""
        queue = self._capture_loop.get_queue()

        while self._running:
            try:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue

                if not frame or not self._running:
                    continue

                # 1. Wake-Word Evaluation
                try:
                    wake_res = await self._wake_word.process_frame(frame)
                    if wake_res.detected:
                        wake_data = WakeWordEventData(
                            wake_word=wake_res.wake_word,
                            confidence=wake_res.confidence,
                            timestamp=wake_res.timestamp,
                            source="voice_engine",
                            metadata=wake_res.metadata,
                        )
                        # Canonical Core Event
                        await event_bus.publish(
                            Event(
                                type=CoreEvents.WAKE_WORD_DETECTED,
                                data=wake_data,
                                source="voice_engine",
                            )
                        )
                        # Legacy alias
                        await event_bus.publish(
                            Event(
                                type="voice.wake_word",
                                data=wake_data,
                                source="voice_engine",
                            )
                        )
                except Exception as e:
                    logger.error(f"Error in wake word processing: {e}", exc_info=True)

                # 2. VAD & Utterance Segmentation
                try:
                    utterance_pcm = self._segmenter.process_frame(frame)
                    if utterance_pcm is not None:
                        sample_rate = getattr(self.config.voice, "sample_rate", 16000)
                        # Non-blocking STT task dispatch: does not stall real-time frame ingestion
                        stt_task = asyncio.create_task(
                            self._dispatch_stt_task(utterance_pcm, sample_rate),
                            name="VoiceEngineSTTTask",
                        )
                        self._stt_tasks.add(stt_task)
                        stt_task.add_done_callback(self._stt_tasks.discard)
                except Exception as e:
                    logger.error(f"Error in utterance segmentation: {e}", exc_info=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in voice pipeline loop: {e}", exc_info=True)

    async def speak(
        self, text: str, voice: str = None, return_visemes: bool = True, **kwargs
    ) -> TTSResponse:
        """Synthesize speech with automatic fallback."""
        if not self._initialized:
            await self.initialize()

        request = TTSRequest(
            text=text, voice=voice, return_visemes=return_visemes, **kwargs
        )

        last_error = None
        for backend in self._tts_backends:
            if not backend.is_available:
                continue

            try:
                logger.debug(f"Trying TTS backend: {backend.name.value}")
                response = await backend.synthesize(request)
                self._current_tts = backend

                # Convert visemes to VRM blend shapes
                if return_visemes and response.visemes:
                    response.visemes = self._convert_visemes(response.visemes, backend.name)

                logger.info(f"TTS success: {backend.name.value} ({response.duration_ms}ms)")
                return response
            except Exception as e:
                last_error = e
                logger.warning(f"TTS backend {backend.name.value} failed: {e}")
                continue

        raise RuntimeError(f"All TTS backends failed. Last error: {last_error}")

    def _convert_visemes(self, visemes: List[Dict], provider: VoiceProvider) -> List[Dict]:
        """Convert provider-specific visemes to VRM blend shape format."""
        converted = []
        for v in visemes:
            if "blend_shapes" in v and "time_ms" in v:
                converted.append(v)
            elif provider == VoiceProvider.AZURE:
                viseme_id = v.get("viseme_id", 0)
                offset = v.get("offset", 0) / 10000  # Convert to ms
                blend_shapes = VisemeMapper.MapAzureViseme(viseme_id)
                converted.append({
                    "time_ms": offset,
                    "blend_shapes": blend_shapes,
                })
            elif provider == VoiceProvider.HEADTTS:
                phoneme = v.get("phoneme", "")
                start = v.get("start", 0)
                end = v.get("end", 0)
                blend_shapes = VisemeMapper.MapPhoneme(phoneme, safe=True)
                converted.append({
                    "time_ms": start * 1000,
                    "end_ms": end * 1000,
                    "blend_shapes": blend_shapes,
                })
            elif "viseme" in v:
                shape = v.get("viseme", "sil")
                converted.append({
                    "viseme": shape,
                    "time_ms": v.get("time_ms", 0),
                    "duration_ms": v.get("duration_ms", 50),
                    "blend_shapes": VisemeMapper.MapOculusViseme(shape, safe=True),
                })
        return converted

    async def transcribe(self, audio_data: bytes, **kwargs) -> STTResponse:
        """Transcribe audio using STT backend."""
        if not self._initialized:
            await self.initialize()

        if not self._stt_backend or not self._stt_backend.is_available:
            raise RuntimeError("No STT backend available")

        request = STTRequest(audio_data=audio_data, **kwargs)
        return await self._stt_backend.transcribe(request)

    async def start_wake_word_listener(self, audio_queue: asyncio.Queue) -> asyncio.Task:
        """Start wake word detection from an external audio queue (backward compatibility)."""
        if not self._wake_word:
            raise RuntimeError("Wake word detector not initialized")

        async def listen():
            await self._wake_word.start()
            while True:
                audio_chunk = await audio_queue.get()
                wake_res = await self._wake_word.process_frame(audio_chunk)
                if wake_res.detected:
                    wake_data = WakeWordEventData(
                        wake_word=wake_res.wake_word,
                        confidence=wake_res.confidence,
                        timestamp=wake_res.timestamp,
                        source="voice_engine",
                    )
                    await event_bus.publish(
                        Event(
                            type=CoreEvents.WAKE_WORD_DETECTED,
                            data=wake_data,
                            source="voice_engine",
                        )
                    )
                    await event_bus.publish(
                        Event(
                            type="voice.wake_word",
                            data=wake_data,
                            source="voice_engine",
                        )
                    )

        return asyncio.create_task(listen())

    async def stop_wake_word_listener(self, task: asyncio.Task):
        """Stop wake word detection task."""
        if self._wake_word:
            await self._wake_word.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def get_status(self) -> Dict:
        return {
            "initialized": self._initialized,
            "running": self._running,
            "current_tts": self._current_tts.name.value if self._current_tts else None,
            "tts_backends": [b.name.value for b in self._tts_backends],
            "stt_backend": self._stt_backend.name if self._stt_backend else None,
            "wake_word": self._wake_word.wake_word if self._wake_word else None,
            "wake_word_active": self._wake_word._running if self._wake_word else False,
            "capture_mode": type(self._capture_source).__name__ if self._capture_source else None,
            "vad_state": self._segmenter.state.value if self._segmenter else None,
        }

    async def close(self):
        """Close all backends and stop pipeline."""
        await self.stop()
        for backend in self._tts_backends:
            if hasattr(backend, "close"):
                await backend.close()

        if self._stt_backend and hasattr(self._stt_backend, "close"):
            await self._stt_backend.close()


from serein_core.config import get_config
from serein_core.events import (
    CoreEvents,
    Event,
    UtteranceEventData,
    WakeWordEventData,
    event_bus,
)

# Global voice engine instance
voice_engine = VoiceEngine()