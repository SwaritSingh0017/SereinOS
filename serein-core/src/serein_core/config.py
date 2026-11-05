"""Configuration management for SereinOS Core Service.
Loads from YAML, environment variables, and provides typed settings.
"""
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml
import platform
import os


def _get_data_dir() -> Path:
    """Get platform-appropriate data directory."""
    if platform.system() == "Windows":
        return Path(os.getenv("LOCALAPPDATA", os.path.expanduser("~"))) / "SereinOS"
    else:
        return Path("/var/lib/serein")


def _get_config_dir() -> Path:
    """Get platform-appropriate config directory."""
    if platform.system() == "Windows":
        return Path(os.getenv("APPDATA", os.path.expanduser("~"))) / "SereinOS"
    else:
        return Path("/etc/serein")


def _get_runtime_dir() -> Path:
    """Get platform-appropriate runtime directory."""
    if platform.system() == "Windows":
        return Path(os.getenv("LOCALAPPDATA", os.path.expanduser("~"))) / "SereinOS" / "runtime"
    else:
        return Path("/run/serein")



def mask_secret(value: Optional[str]) -> str:
    """Mask sensitive tokens or keys for safe display in logs and telemetry."""
    if not value:
        return "<not_set>"
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}****{value[-4:]}"

def _get_log_dir() -> Path:
    """Get platform-appropriate log directory."""
    if platform.system() == "Windows":
        return Path(os.getenv("LOCALAPPDATA", os.path.expanduser("~"))) / "SereinOS" / "logs"
    else:
        return Path("/var/log/serein")


class DatabaseConfig(BaseSettings):
    """Database configuration."""
    # Use custom property to completely bypass env var parsing for path
    _path: Path = None
    
    @property
    def path(self) -> Path:
        if self._path is None:
            return _get_data_dir()
        return self._path
    
    @path.setter
    def path(self, value: Path):
        self._path = value
    
    encryption_key: Optional[str] = Field(default=None, description="SQLCipher encryption key")
    pragmas: Dict[str, str] = Field(default_factory=lambda: {
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "cache_size": "-32768",
        "temp_store": "MEMORY",
        "busy_timeout": "5000",
    })
    
    model_config = SettingsConfigDict(
        env_prefix="IGNORE_",
        case_sensitive=False,
        extra="ignore",
    )


class AIProviderConfig(BaseSettings):
    """AI Provider configuration."""
    provider: str = Field(default="auto", description="auto, nvidia, openrouter, ollama, local")
    models: Dict[str, str] = Field(default_factory=lambda: {
        "fast": "meta/llama-3.1-8b-instruct",
        "smart": "meta/llama-3.3-70b-instruct",
        "local": "llama3.1:8b",
    })
    nvidia_api_key: Optional[str] = Field(default=None)
    openrouter_api_key: Optional[str] = Field(default=None)
    ollama_base_url: str = Field(default="http://localhost:11434")
    local_model_path: Optional[Path] = Field(default=None)
    request_timeout: float = Field(default=30.0)
    max_tokens: int = Field(default=2048)
    temperature: float = Field(default=0.7)

    def get_masked_keys(self) -> Dict[str, str]:
        """Return masked representation of API keys."""
        return {
            "nvidia_api_key": mask_secret(self.nvidia_api_key),
            "openrouter_api_key": mask_secret(self.openrouter_api_key),
        }


class VoiceConfig(BaseSettings):
    """Voice stack configuration."""
    enabled: bool = Field(default=True)
    tts_provider: str = Field(default="auto", description="auto, azure, headtts, piper")
    stt_provider: str = Field(default="whisper-cpp", description="whisper, whisper-cpp, vosk")
    wake_word: str = Field(default="serein")
    
    # Azure TTS
    azure_speech_key: Optional[str] = Field(default=None)
    azure_speech_region: str = Field(default="eastus")
    azure_voice: str = Field(default="en-US-AriaNeural")
    
    # HeadTTS (local)
    headtts_url: str = Field(default="http://localhost:5000")
    
    # Piper TTS (local fallback)
    piper_voice: str = Field(default="en_US-lessac-medium")
    piper_binary: Path = Field(default=Path("/usr/bin/piper"))
    
    # Whisper.cpp
    whisper_model: str = Field(default="base")
    whisper_cpp_path: Path = Field(default=Path("/usr/bin/whisper-cli"))
    
    # Audio
    sample_rate: int = Field(default=16000)
    channels: int = Field(default=1)
    chunk_size: int = Field(default=1024)
    
    # VAD & Wake Word & Mock
    vad_energy_threshold: float = Field(default=0.015, description="RMS energy threshold for speech activity")
    vad_silence_duration: float = Field(default=1.5, description="Silence duration in seconds to trigger utterance completion")
    vad_preroll_duration: float = Field(default=0.3, description="Pre-roll buffer duration in seconds")
    vad_min_speech_duration: float = Field(default=0.4, description="Minimum speech duration in seconds")
    vad_max_speech_duration: float = Field(default=15.0, description="Maximum speech duration in seconds")
    wake_phrase: str = Field(default="Hey Serein", description="Activation wake phrase")
    wake_sensitivity: float = Field(default=0.5, description="Wake word detection sensitivity")
    wake_debounce_duration: float = Field(default=1.5, description="Debounce refractory period in seconds")
    capture_mode: str = Field(default="headless", description="Audio capture mode: 'headless' or 'hardware'")
    mock_wake_word: bool = Field(default=True, description="Enable deterministic mock wake-word engine for headless CI")
    mock_stt: bool = Field(default=True, description="Enable deterministic mock STT for headless CI")


class SchedulerConfig(BaseSettings):
    """Scheduler configuration."""
    enabled: bool = Field(default=True)
    max_concurrent_jobs: int = Field(default=10)
    timezone: str = Field(default="UTC")
    persistence_path: Path = Field(default_factory=lambda: _get_data_dir() / "scheduler")


class CommandBusConfig(BaseSettings):
    """Command Bus configuration."""
    enabled: bool = Field(default=True)
    dbus_service_name: str = Field(default="os.serein.CommandBus")
    dbus_object_path: str = Field(default="/os/serein/CommandBus")
    permission_tiers: Dict[str, List[str]] = Field(default_factory=lambda: {
        "read_only": [
            "org.freedesktop.DBus.Properties.Get",
            "org.freedesktop.systemd1.Manager.ListUnits",
            "org.gnome.Shell.Eval",
        ],
        "reversible": [
            "org.freedesktop.systemd1.Unit.Start",
            "org.freedesktop.systemd1.Unit.Stop",
            "org.freedesktop.Notifications.Notify",
        ],
        "destructive": [
            "org.freedesktop.systemd1.Manager.PowerOff",
            "org.freedesktop.systemd1.Manager.Reboot",
            "org.freedesktop.PolicyKit1.Authority.CheckAuthorization",
        ],
        "sensitive": [
            "org.freedesktop.secrets.Service.OpenSession",
            "org.freedesktop.NetworkManager.Settings.AddConnection",
        ],
    })


class SecurityConfig(BaseSettings):
    """Security & privacy configuration."""
    sqlcipher_enabled: bool = Field(default=True)
    audit_log_path: Path = Field(default=Path("/var/log/serein/audit.log"))
    permissions_db_path: Path = Field(default=Path("/var/lib/serein/permissions.db"))
    isolated_user_state: bool = Field(default=True)
    self_modification_level: int = Field(default=1, ge=0, le=2, description="0=observe, 1=suggest, 2=patch with approval")
    debug_mode: bool = Field(default=False, description="Disable debugging in production")
    age_verification_enforced: bool = Field(default=True, description="Enforce age confirmation gate")
    rate_limit_per_minute: int = Field(default=60, description="Rate limit per client minute")


class LoggingConfig(BaseSettings):
    """Logging configuration."""
    level: str = Field(default="INFO")
    format: str = Field(default="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}")
    file_path: Path = Field(default=Path("/var/log/serein/core.log"))
    rotation: str = Field(default="10 MB")
    retention: str = Field(default="7 days")
    compression: str = Field(default="gz")


class CoreConfig(BaseSettings):
    """Main configuration aggregator."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
    )
    
    # Service
    service_name: str = "serein-core"
    data_dir: Path = Field(default=Path("/var/lib/serein"))
    config_dir: Path = Field(default=Path("/etc/serein"))
    runtime_dir: Path = Field(default=Path("/run/serein"))
    
    # Sub-configs
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    ai: AIProviderConfig = Field(default_factory=AIProviderConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    command_bus: CommandBusConfig = Field(default_factory=CommandBusConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    
    @classmethod
    def from_yaml(cls, path: Path) -> "CoreConfig":
        """Load configuration from YAML file."""
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()
    
    def to_yaml(self, path: Path) -> None:
        """Save configuration to YAML file with strict 0600 file permissions."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
        data = self.model_dump(mode="json")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        try:
            fd = os.open(path, flags, mode)
            with open(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
            if os.name != "nt":
                os.chmod(path, 0o600)
        except Exception:
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
            if os.name != "nt":
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass


# Global config instance
_config_instance: CoreConfig | None = None


def load_config(config_path: Path | None = None) -> CoreConfig:
    """Load configuration from YAML file."""
    global _config_instance
    
    if config_path is None:
        config_path = Path("/etc/serein/config.yaml")
    
    if _config_instance is None:
        _config_instance = CoreConfig.from_yaml(config_path)
    
    return _config_instance


def get_config() -> CoreConfig:
    """Get the loaded configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance


def __getattr__(name: str) -> Any:
    """Module-level attribute lookup for backward compatibility."""
    if name == "config":
        return get_config()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
