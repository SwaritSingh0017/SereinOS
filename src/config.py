"""
Configuration system for SereinOS.
Handles settings, API keys, and environment configuration.
"""
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class LLMConfig:
    """LLM provider configuration."""
    provider: str = "nvidia"  # nvidia, ollama, openrouter
    model: str = "meta/llama-3.3-70b-instruct"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 500
    timeout: int = 60


@dataclass
class DatabaseConfig:
    """Database configuration."""
    path: str = "data/database/serein.db"


@dataclass
class VoiceConfig:
    """Voice module configuration."""
    enabled: bool = False
    stt_model: str = "whisper-base"
    tts_model: str = "piper"
    wake_word: str = "serein"
    language: str = "en"


@dataclass
class VisionConfig:
    """Vision module configuration."""
    enabled: bool = False
    camera_index: int = 0
    ocr_enabled: bool = True


@dataclass
class SafetyConfig:
    """Safety layer configuration."""
    strict_mode: bool = True
    require_approval_for: list = None
    
    def __post_init__(self):
        if self.require_approval_for is None:
            self.require_approval_for = ["file_delete", "system_command", "self_modify"]


@dataclass
class SereinConfig:
    """Main SereinOS configuration."""
    llm: LLMConfig = None
    database: DatabaseConfig = None
    voice: VoiceConfig = None
    vision: VisionConfig = None
    safety: SafetyConfig = None
    debug: bool = False
    log_level: str = "INFO"
    
    def __post_init__(self):
        if self.llm is None:
            self.llm = LLMConfig()
        if self.database is None:
            self.database = DatabaseConfig()
        if self.voice is None:
            self.voice = VoiceConfig()
        if self.vision is None:
            self.vision = VisionConfig()
        if self.safety is None:
            self.safety = SafetyConfig()


class ConfigManager:
    """Manages SereinOS configuration."""
    
    CONFIG_FILE = "config/serein_config.json"
    ENV_FILE = ".env"
    
    def __init__(self):
        self.config: Optional[SereinConfig] = None
        self._load()
    
    def _load(self) -> None:
        """Load configuration from file and environment."""
        # Start with defaults
        self.config = SereinConfig()
        
        # Load from config file if exists
        config_path = Path(self.CONFIG_FILE)
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    data = json.load(f)
                self._apply_config_dict(data)
            except Exception as e:
                print(f"[Config] Warning: Failed to load config file: {e}")
        
        # Override with environment variables
        self._load_from_env()
        
        # Ensure directories exist
        self._ensure_directories()
    
    def _apply_config_dict(self, data: Dict) -> None:
        """Applies configuration from dictionary."""
        if "llm" in data:
            self.config.llm = LLMConfig(**data["llm"])
        if "database" in data:
            self.config.database = DatabaseConfig(**data["database"])
        if "voice" in data:
            self.config.voice = VoiceConfig(**data["voice"])
        if "vision" in data:
            self.config.vision = VisionConfig(**data["vision"])
        if "safety" in data:
            self.config.safety = SafetyConfig(**data["safety"])
        if "debug" in data:
            self.config.debug = data["debug"]
        if "log_level" in data:
            self.config.log_level = data["log_level"]
    
    def _load_from_env(self) -> None:
        """Loads configuration from environment variables."""
        # LLM settings
        if api_key := os.getenv("NVIDIA_API_KEY"):
            self.config.llm.api_key = api_key
        if api_key := os.getenv("OPENROUTER_API_KEY"):
            self.config.llm.api_key = api_key
        if model := os.getenv("LLM_MODEL"):
            self.config.llm.model = model
        if provider := os.getenv("LLM_PROVIDER"):
            self.config.llm.provider = provider
        if base_url := os.getenv("LLM_BASE_URL"):
            self.config.llm.base_url = base_url
        
        # Database
        if db_path := os.getenv("DATABASE_PATH"):
            self.config.database.path = db_path
        
        # Debug
        if os.getenv("DEBUG", "").lower() in ("true", "1", "yes"):
            self.config.debug = True
        
        if log_level := os.getenv("LOG_LEVEL"):
            self.config.log_level = log_level
    
    def _ensure_directories(self) -> None:
        """Ensures required directories exist."""
        dirs = [
            "data/database",
            "data/logs",
            "data/memories",
            "data/profiles",
            "config",
            "assets"
        ]
        for d in dirs:
            Path(d).mkdir(parents=True, exist_ok=True)
    
    def save(self) -> None:
        """Saves current configuration to file."""
        config_path = Path(self.CONFIG_FILE)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "llm": asdict(self.config.llm),
            "database": asdict(self.config.database),
            "voice": asdict(self.config.voice),
            "vision": asdict(self.config.vision),
            "safety": asdict(self.config.safety),
            "debug": self.config.debug,
            "log_level": self.config.log_level
        }
        
        try:
            with open(config_path, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"[Config] Saved to {config_path}")
        except Exception as e:
            print(f"[Config] Error saving: {e}")
    
    def get(self) -> SereinConfig:
        """Returns current configuration."""
        return self.config
    
    def update_llm_config(self, **kwargs) -> None:
        """Updates LLM configuration."""
        for key, value in kwargs.items():
            if hasattr(self.config.llm, key):
                setattr(self.config.llm, key, value)
        self.save()
    
    def create_env_template(self) -> None:
        """Creates a .env.template file."""
        template = """# SereinOS Environment Variables
# Copy this to .env and fill in your values

# LLM Provider (nvidia, ollama, openrouter)
LLM_PROVIDER=nvidia
LLM_MODEL=meta/llama-3.3-70b-instruct
LLM_BASE_URL=

# API Keys (only need one based on provider)
NVIDIA_API_KEY=your_nvidia_api_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here

# Database
DATABASE_PATH=data/database/serein.db

# Debug
DEBUG=false
LOG_LEVEL=INFO
"""
        env_path = Path(self.ENV_FILE + ".template")
        with open(env_path, 'w') as f:
            f.write(template)
        print(f"[Config] Created {env_path}")


# Global config manager
config_manager = ConfigManager()