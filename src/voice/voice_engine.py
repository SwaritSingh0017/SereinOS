"""
Voice Engine - Speech-to-Text and Text-to-Speech for SereinOS.
Supports multiple backends with automatic fallback.
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Callable
import threading
import queue
import time
import os
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VoiceConfig:
    """Voice module configuration."""
    enabled: bool = False
    stt_backend: str = "whisper"  # whisper, faster-whisper, vosk
    tts_backend: str = "piper"    # piper, coqui, system
    wake_word: str = "serein"
    language: str = "en"
    sample_rate: int = 16000
    chunk_size: int = 1024
    silence_threshold: float = 0.01
    silence_duration: float = 1.5  # seconds of silence to end recording
    whisper_model: str = "base"    # tiny, base, small, medium, large
    piper_voice: str = "en_US-lessac-medium"
    device: str = "cpu"            # cpu, cuda


class STTBackend(ABC):
    """Abstract base class for Speech-to-Text backends."""
    
    @abstractmethod
    def transcribe(self, audio_data: bytes) -> str:
        """Transcribe audio bytes to text."""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if backend is available."""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass


class TTSBackend(ABC):
    """Abstract base class for Text-to-Speech backends."""
    
    @abstractmethod
    def synthesize(self, text: str, output_path: str) -> bool:
        """Synthesize text to audio file."""
        pass
    
    @abstractmethod
    def speak(self, text: str) -> bool:
        """Speak text directly (streaming)."""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass


class WhisperSTT(STTBackend):
    """OpenAI Whisper STT backend."""
    
    def __init__(self, model_name: str = "base", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None
        self._load_model()
    
    def _load_model(self):
        try:
            import whisper
            self._model = whisper.load_model(self.model_name, device=self.device)
        except ImportError:
            self._model = None
        except Exception as e:
            print(f"[WhisperSTT] Failed to load model: {e}")
            self._model = None
    
    def is_available(self) -> bool:
        return self._model is not None
    
    @property
    def name(self) -> str:
        return "whisper"
    
    def transcribe(self, audio_data: bytes) -> str:
        if not self._model:
            return ""
        
        import tempfile
        import numpy as np
        
        # Save audio to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name
        
        try:
            result = self._model.transcribe(temp_path, fp16=False)
            return result.get("text", "").strip()
        finally:
            os.unlink(temp_path)


class FasterWhisperSTT(STTBackend):
    """Faster-Whisper STT backend (optimized)."""
    
    def __init__(self, model_name: str = "base", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None
        self._load_model()
    
    def _load_model(self):
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device=self.device, compute_type="int8")
        except ImportError:
            self._model = None
        except Exception as e:
            print(f"[FasterWhisperSTT] Failed to load: {e}")
            self._model = None
    
    def is_available(self) -> bool:
        return self._model is not None
    
    @property
    def name(self) -> str:
        return "faster-whisper"
    
    def transcribe(self, audio_data: bytes) -> str:
        if not self._model:
            return ""
        
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name
        
        try:
            segments, _ = self._model.transcribe(temp_path, beam_size=5)
            return " ".join([seg.text for seg in segments]).strip()
        finally:
            os.unlink(temp_path)


class VoskSTT(STTBackend):
    """Vosk offline STT backend."""
    
    def __init__(self, model_path: str = None, language: str = "en"):
        self.language = language
        self._model = None
        self._rec = None
        self._load_model(model_path)
    
    def _load_model(self, model_path: str = None):
        try:
            from vosk import Model, KaldiRecognizer
            
            if model_path and os.path.exists(model_path):
                self._model = Model(model_path)
            else:
                # Try default model locations
                default_paths = [
                    f"models/vosk-model-{self.language}",
                    f"/usr/share/vosk/models/vosk-model-{self.language}",
                    os.path.expanduser(f"~/vosk-model-{self.language}")
                ]
                for p in default_paths:
                    if os.path.exists(p):
                        self._model = Model(p)
                        break
            
            if self._model:
                self._rec = KaldiRecognizer(self._model, 16000)
        except ImportError:
            self._model = None
        except Exception as e:
            print(f"[VoskSTT] Failed to load: {e}")
            self._model = None
    
    def is_available(self) -> bool:
        return self._model is not None
    
    @property
    def name(self) -> str:
        return "vosk"
    
    def transcribe(self, audio_data: bytes) -> str:
        if not self._rec:
            return ""
        
        import json
        
        # Process in chunks
        self._rec.AcceptWaveform(audio_data)
        result = json.loads(self._rec.FinalResult())
        return result.get("text", "").strip()


class PiperTTS(TTSBackend):
    """Piper TTS backend (fast, offline)."""
    
    def __init__(self, voice: str = "en_US-lessac-medium"):
        self.voice = voice
        self._piper_path = self._find_piper()
        self._voice_path = self._find_voice(voice)
    
    def _find_piper(self) -> Optional[str]:
        # Check common locations
        paths = [
            "piper/piper.exe" if os.name == 'nt' else "piper/piper",
            "/usr/local/bin/piper",
            "/usr/bin/piper",
            os.path.expanduser("~/piper/piper"),
            "piper"
        ]
        for p in paths:
            if os.path.exists(p) or self._which(p):
                return p
        return None
    
    def _which(self, cmd: str) -> Optional[str]:
        import shutil
        return shutil.which(cmd)
    
    def _find_voice(self, voice: str) -> Optional[str]:
        paths = [
            f"voices/{voice}.onnx",
            f"/usr/share/piper-voices/{voice}.onnx",
            os.path.expanduser(f"~/piper-voices/{voice}.onnx"),
        ]
        for p in paths:
            if os.path.exists(p):
                return p
        return None
    
    def is_available(self) -> bool:
        return self._piper_path is not None and self._voice_path is not None
    
    @property
    def name(self) -> str:
        return "piper"
    
    def synthesize(self, text: str, output_path: str) -> bool:
        if not self.is_available():
            return False
        
        import subprocess
        
        try:
            cmd = [
                self._piper_path,
                "--model", self._voice_path,
                "--output_file", output_path
            ]
            proc = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=30)
            return proc.returncode == 0 and os.path.exists(output_path)
        except Exception as e:
            print(f"[PiperTTS] Synthesis failed: {e}")
            return False
    
    def speak(self, text: str) -> bool:
        if not self.is_available():
            return False
        
        import subprocess
        import tempfile
        
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_path = f.name
            
            if self.synthesize(text, temp_path):
                # Play the audio
                if os.name == 'nt':
                    subprocess.run(["powershell", "-c", f'(New-Object Media.SoundPlayer "{temp_path}").PlaySync()'])
                else:
                    subprocess.run(["aplay", temp_path])
                os.unlink(temp_path)
                return True
            return False
        except Exception as e:
            print(f"[PiperTTS] Speak failed: {e}")
            return False


class CoquiTTS(TTSBackend):
    """Coqui TTS backend."""
    
    def __init__(self, model_name: str = "tts_models/en/ljspeech/tacotron2-DDC"):
        self.model_name = model_name
        self._tts = None
        self._load_model()
    
    def _load_model(self):
        try:
            from TTS.api import TTS
            self._tts = TTS(self.model_name)
        except ImportError:
            self._tts = None
        except Exception as e:
            print(f"[CoquiTTS] Failed to load: {e}")
            self._tts = None
    
    def is_available(self) -> bool:
        return self._tts is not None
    
    @property
    def name(self) -> str:
        return "coqui"
    
    def synthesize(self, text: str, output_path: str) -> bool:
        if not self._tts:
            return False
        
        try:
            self._tts.tts_to_file(text=text, file_path=output_path)
            return os.path.exists(output_path)
        except Exception as e:
            print(f"[CoquiTTS] Synthesis failed: {e}")
            return False
    
    def speak(self, text: str) -> bool:
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
        
        try:
            if self.synthesize(text, temp_path):
                # Play using system
                if os.name == 'nt':
                    import subprocess
                    subprocess.run(["powershell", "-c", f'(New-Object Media.SoundPlayer "{temp_path}").PlaySync()'])
                else:
                    import subprocess
                    subprocess.run(["aplay", temp_path])
                os.unlink(temp_path)
                return True
            return False
        except Exception as e:
            print(f"[CoquiTTS] Speak failed: {e}")
            return False


class SystemTTS(TTSBackend):
    """System built-in TTS (Windows SAPI, macOS say, Linux espeak)."""
    
    def is_available(self) -> bool:
        return True  # Always available
    
    @property
    def name(self) -> str:
        return "system"
    
    def synthesize(self, text: str, output_path: str) -> bool:
        try:
            if os.name == 'nt':
                # Windows - use PowerShell to save to file
                import subprocess
                ps_script = f'''
                Add-Type -AssemblyName System.Speech
                $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
                $synth.SetOutputToWaveFile("{output_path}")
                $synth.Speak("{text.replace('"', '""')}")
                $synth.Dispose()
                '''
                subprocess.run(["powershell", "-Command", ps_script], check=True)
            else:
                # Linux/macOS - use espeak or say
                import subprocess
                if os.path.exists("/usr/bin/say"):
                    subprocess.run(["say", "-o", output_path, text], check=True)
                else:
                    subprocess.run(["espeak", "-w", output_path, text], check=True)
            return os.path.exists(output_path)
        except Exception as e:
            print(f"[SystemTTS] Synthesis failed: {e}")
            return False
    
    def speak(self, text: str) -> bool:
        try:
            if os.name == 'nt':
                import subprocess
                ps_script = f'''
                Add-Type -AssemblyName System.Speech
                $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
                $synth.Speak("{text.replace('"', '""')}")
                $synth.Dispose()
                '''
                subprocess.run(["powershell", "-Command", ps_script], check=True)
            else:
                import subprocess
                if os.path.exists("/usr/bin/say"):
                    subprocess.run(["say", text], check=True)
                else:
                    subprocess.run(["espeak", text], check=True)
            return True
        except Exception as e:
            print(f"[SystemTTS] Speak failed: {e}")
            return False


class VoiceEngine:
    """Main voice engine coordinating STT and TTS."""
    
    def __init__(self, config: VoiceConfig = None):
        self.config = config or VoiceConfig()
        self._stt: Optional[STTBackend] = None
        self._tts: Optional[TTSBackend] = None
        self._listening = False
        self._audio_queue = queue.Queue()
        self._callbacks: Dict[str, List[Callable]] = {
            "on_wake_word": [],
            "on_transcript": [],
            "on_speak_start": [],
            "on_speak_end": []
        }
        self._initialize_backends()
    
    def _initialize_backends(self):
        """Initialize STT and TTS backends with fallback."""
        # STT Backends (in order of preference)
        stt_backends = []
        
        if self.config.stt_backend in ["faster-whisper", "auto"]:
            stt_backends.append(FasterWhisperSTT(self.config.whisper_model, self.config.device))
        
        if self.config.stt_backend in ["whisper", "auto"]:
            stt_backends.append(WhisperSTT(self.config.whisper_model, self.config.device))
        
        if self.config.stt_backend in ["vosk", "auto"]:
            stt_backends.append(VoskSTT(language=self.config.language))
        
        for backend in stt_backends:
            if backend.is_available():
                self._stt = backend
                print(f"[VoiceEngine] STT: {backend.name}")
                break
        
        if not self._stt:
            print("[VoiceEngine] No STT backend available")
        
        # TTS Backends (in order of preference)
        tts_backends = []
        
        if self.config.tts_backend in ["piper", "auto"]:
            tts_backends.append(PiperTTS(self.config.piper_voice))
        
        if self.config.tts_backend in ["coqui", "auto"]:
            tts_backends.append(CoquiTTS())
        
        # System TTS always as fallback
        tts_backends.append(SystemTTS())
        
        for backend in tts_backends:
            if backend.is_available():
                self._tts = backend
                print(f"[VoiceEngine] TTS: {backend.name}")
                break
    
    def on(self, event: str, callback: Callable):
        """Register event callback."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)
    
    def _emit(self, event: str, *args):
        """Emit event to callbacks."""
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args)
            except Exception as e:
                print(f"[VoiceEngine] Callback error: {e}")
    
    def transcribe(self, audio_data: bytes) -> str:
        """Transcribe audio data to text."""
        if not self._stt:
            return ""
        return self._stt.transcribe(audio_data)
    
    def speak(self, text: str, blocking: bool = True) -> bool:
        """Speak text using TTS."""
        if not self._tts or not text.strip():
            return False
        
        self._emit("on_speak_start", text)
        
        if blocking:
            result = self._tts.speak(text)
        else:
            # Async speak
            threading.Thread(target=self._tts.speak, args=(text,), daemon=True).start()
            result = True
        
        self._emit("on_speak_end", text)
        return result
    
    def is_listening(self) -> bool:
        return self._listening
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "listening": self._listening,
            "stt": self._stt.name if self._stt else "none",
            "tts": self._tts.name if self._tts else "none",
            "wake_word": self.config.wake_word,
            "language": self.config.language
        }


# Audio recording helper
class AudioRecorder:
    """Records audio from microphone."""
    
    def __init__(self, sample_rate: int = 16000, chunk_size: int = 1024):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self._stream = None
        self._audio = None
        self._recording = False
        self._frames = []
    
    def start(self):
        """Start recording."""
        try:
            import pyaudio
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
            self._recording = True
            self._frames = []
        except ImportError:
            print("[AudioRecorder] pyaudio not installed")
            return False
        except Exception as e:
            print(f"[AudioRecorder] Failed to start: {e}")
            return False
        return True
    
    def read_chunk(self) -> bytes:
        """Read one chunk of audio."""
        if self._stream and self._recording:
            return self._stream.read(self.chunk_size, exception_on_overflow=False)
        return b""
    
    def stop(self) -> bytes:
        """Stop recording and return all audio data."""
        self._recording = False
        
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        
        if self._audio:
            self._audio.terminate()
            self._audio = None
        
        return b"".join(self._frames)
    
    def record_with_vad(self, silence_threshold: float = 0.01, 
                        silence_duration: float = 1.5) -> bytes:
        """Record until voice activity detection detects silence."""
        if not self.start():
            return b""
        
        import audioop
        silent_chunks = 0
        max_silent_chunks = int(silence_duration * self.sample_rate / self.chunk_size)
        speaking = False
        
        try:
            while self._recording:
                chunk = self.read_chunk()
                if not chunk:
                    break
                
                self._frames.append(chunk)
                
                # Calculate RMS
                rms = audioop.rms(chunk, 2) / 32768.0
                
                if rms > silence_threshold:
                    speaking = True
                    silent_chunks = 0
                elif speaking:
                    silent_chunks += 1
                    if silent_chunks >= max_silent_chunks:
                        break
        finally:
            self.stop()
        
        return b"".join(self._frames)