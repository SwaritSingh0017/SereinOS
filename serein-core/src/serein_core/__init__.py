"""
SereinOS Core Service - Always-on daemon providing:
- D-Bus Command Bus for system control
- Scheduler for reminders/tasks
- Memory & persistence (SQLCipher)
- AI Brain Router with provider abstraction
- Event bus for inter-component communication
"""

from .main import SereinCore
from .config import load_config, CoreConfig as SereinConfig
from .context import ContextEngine, get_context_engine
from .decision import DecisionEngine, get_decision_engine, ActionType, Decision
from .agent import AgentEngine, ActionStatus
from .safety import SafetyLayer, PermissionLevel, ActionRisk
from .vision_engine import VisionEngine, VisionConfig
from .voice_engine import VoiceEngine, VoiceConfig

__version__ = "0.1.0"
__author__ = "Swarit Singh"

__all__ = [
    "SereinCore", "load_config", "SereinConfig",
    "ContextEngine", "get_context_engine",
    "DecisionEngine", "get_decision_engine", "ActionType", "Decision",
    "AgentEngine", "ActionStatus",
    "SafetyLayer", "PermissionLevel", "ActionRisk",
    "VisionEngine", "VisionConfig",
    "VoiceEngine", "VoiceConfig"
]