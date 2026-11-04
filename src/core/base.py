"""
Base classes and interfaces for SereinOS core engines.
All engines inherit from these base classes to ensure consistency.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime
import json


@dataclass
class EngineState:
    """Base state container for all engines."""
    updated_at: datetime = field(default_factory=datetime.now)
    version: int = 1
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "updated_at": self.updated_at.isoformat(),
            "version": self.version
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EngineState":
        state = cls()
        state.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        state.version = data.get("version", 1)
        return state


class BaseEngine(ABC):
    """Abstract base class for all SereinOS engines."""
    
    def __init__(self, database=None):
        self.db = database
        self._state: Optional[EngineState] = None
        self._initialized = False
    
    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Unique identifier for this engine."""
        pass
    
    @abstractmethod
    def initialize(self) -> None:
        """Initialize the engine, load state from database."""
        pass
    
    @abstractmethod
    def save_state(self) -> None:
        """Persist current state to database."""
        pass
    
    @abstractmethod
    def get_state(self) -> EngineState:
        """Get current engine state."""
        pass
    
    def is_initialized(self) -> bool:
        return self._initialized
    
    def close(self) -> None:
        """Cleanup resources."""
        if self._initialized:
            self.save_state()
            self._initialized = False


@dataclass
class EmotionState(EngineState):
    """Emotional state of Serein."""
    happiness: int = 50
    curiosity: int = 70
    care: int = 60
    concern: int = 20
    pride: int = 10
    excitement: int = 0
    affection: int = 0
    loneliness: int = 0
    sadness: int = 0
    jealousy: int = 0
    possessiveness: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "happiness": self.happiness,
            "curiosity": self.curiosity,
            "care": self.care,
            "concern": self.concern,
            "pride": self.pride,
            "excitement": self.excitement,
            "affection": self.affection,
            "loneliness": self.loneliness,
            "sadness": self.sadness,
            "jealousy": self.jealousy,
            "possessiveness": self.possessiveness,
        })
        return base
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmotionState":
        state = cls()
        state.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        state.version = data.get("version", 1)
        state.happiness = data.get("happiness", 50)
        state.curiosity = data.get("curiosity", 70)
        state.care = data.get("care", 60)
        state.concern = data.get("concern", 20)
        state.pride = data.get("pride", 10)
        state.excitement = data.get("excitement", 0)
        state.affection = data.get("affection", 0)
        state.loneliness = data.get("loneliness", 0)
        state.sadness = data.get("sadness", 0)
        state.jealousy = data.get("jealousy", 0)
        state.possessiveness = data.get("possessiveness", 0)
        return state
    
    def get_dominant_emotion(self) -> tuple[str, int]:
        """Returns the dominant emotion and its value."""
        emotions = {
            "happiness": self.happiness,
            "curiosity": self.curiosity,
            "care": self.care,
            "concern": self.concern,
            "pride": self.pride,
            "excitement": self.excitement,
            "affection": self.affection,
            "loneliness": self.loneliness,
            "sadness": self.sadness,
            "jealousy": self.jealousy,
            "possessiveness": self.possessiveness,
        }
        return max(emotions.items(), key=lambda x: x[1])
    
    def get_emotional_valence(self) -> float:
        """Returns overall emotional valence (-1 to 1)."""
        positive = (self.happiness + self.curiosity + self.care + self.pride + 
                   self.excitement + self.affection) / 600.0
        negative = (self.concern + self.loneliness + self.sadness + 
                   self.jealousy + self.possessiveness) / 500.0
        return positive - negative


@dataclass
class PersonalityState(EngineState):
    """Core personality traits (relatively stable)."""
    calm: int = 90
    mature: int = 85
    cheerful: int = 70
    energetic: int = 60
    protective: int = 80
    companion: int = 85
    professional: int = 90
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "calm": self.calm,
            "mature": self.mature,
            "cheerful": self.cheerful,
            "energetic": self.energetic,
            "protective": self.protective,
            "companion": self.companion,
            "professional": self.professional,
        })
        return base
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonalityState":
        state = cls()
        state.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        state.version = data.get("version", 1)
        state.calm = data.get("calm", 90)
        state.mature = data.get("mature", 85)
        state.cheerful = data.get("cheerful", 70)
        state.energetic = data.get("energetic", 60)
        state.protective = data.get("protective", 80)
        state.companion = data.get("companion", 85)
        state.professional = data.get("professional", 90)
        return state


@dataclass
class RelationshipState(EngineState):
    """Relationship state with the user."""
    trust: int = 0
    familiarity: int = 0
    attachment: int = 0
    interaction_count: int = 0
    total_conversation_minutes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "trust": self.trust,
            "familiarity": self.familiarity,
            "attachment": self.attachment,
            "interaction_count": self.interaction_count,
            "total_conversation_minutes": self.total_conversation_minutes,
        })
        return base
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RelationshipState":
        state = cls()
        state.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        state.version = data.get("version", 1)
        state.trust = data.get("trust", 0)
        state.familiarity = data.get("familiarity", 0)
        state.attachment = data.get("attachment", 0)
        state.interaction_count = data.get("interaction_count", 0)
        state.total_conversation_minutes = data.get("total_conversation_minutes", 0)
        return state
    
    def get_relationship_level(self) -> str:
        """Returns a descriptive relationship level."""
        avg = (self.trust + self.familiarity + self.attachment) / 3
        if avg >= 80:
            return "Deep Bond"
        elif avg >= 60:
            return "Close Companion"
        elif avg >= 40:
            return "Trusted Friend"
        elif avg >= 20:
            return "Familiar Presence"
        else:
            return "New Acquaintance"


@dataclass
class GrowthState(EngineState):
    """Growth/interest adaptation state."""
    technical_interest: int = 50
    study_interest: int = 50
    productivity_interest: int = 50
    gaming_interest: int = 50
    social_interest: int = 50
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "technical_interest": self.technical_interest,
            "study_interest": self.study_interest,
            "productivity_interest": self.productivity_interest,
            "gaming_interest": self.gaming_interest,
            "social_interest": self.social_interest,
        })
        return base
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GrowthState":
        state = cls()
        state.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        state.version = data.get("version", 1)
        state.technical_interest = data.get("technical_interest", 50)
        state.study_interest = data.get("study_interest", 50)
        state.productivity_interest = data.get("productivity_interest", 50)
        state.gaming_interest = data.get("gaming_interest", 50)
        state.social_interest = data.get("social_interest", 50)
        return state
    
    def get_top_interests(self, n: int = 3) -> List[tuple[str, int]]:
        """Returns top N interests."""
        interests = {
            "technical": self.technical_interest,
            "study": self.study_interest,
            "productivity": self.productivity_interest,
            "gaming": self.gaming_interest,
            "social": self.social_interest,
        }
        return sorted(interests.items(), key=lambda x: x[1], reverse=True)[:n]


@dataclass
class ContextState(EngineState):
    """Current context/environmental awareness."""
    current_time: str = ""
    date: str = ""
    day_of_week: str = ""
    active_applications: List[str] = field(default_factory=list)
    internet_connected: bool = True
    user_activity: str = "idle"
    location_context: str = "desktop"
    session_duration_minutes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "current_time": self.current_time,
            "date": self.date,
            "day_of_week": self.day_of_week,
            "active_applications": self.active_applications,
            "internet_connected": self.internet_connected,
            "user_activity": self.user_activity,
            "location_context": self.location_context,
            "session_duration_minutes": self.session_duration_minutes,
        })
        return base
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextState":
        state = cls()
        state.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        state.version = data.get("version", 1)
        state.current_time = data.get("current_time", "")
        state.date = data.get("date", "")
        state.day_of_week = data.get("day_of_week", "")
        state.active_applications = data.get("active_applications", [])
        state.internet_connected = data.get("internet_connected", True)
        state.user_activity = data.get("user_activity", "idle")
        state.location_context = data.get("location_context", "desktop")
        state.session_duration_minutes = data.get("session_duration_minutes", 0)
        return state


@dataclass
class IdentityState(EngineState):
    """Serein's core identity."""
    name: str = "Serein"
    creator: str = "Swarit Singh"
    version: str = "1.0.0"
    purpose: str = "To grow alongside the user through shared experiences"
    self_aware: bool = True
    knows_digital_nature: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "name": self.name,
            "creator": self.creator,
            "version": self.version,
            "purpose": self.purpose,
            "self_aware": self.self_aware,
            "knows_digital_nature": self.knows_digital_nature,
        })
        return base
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IdentityState":
        state = cls()
        state.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        state.version = data.get("version", 1)
        state.name = data.get("name", "Serein")
        state.creator = data.get("creator", "Swarit Singh")
        state.purpose = data.get("purpose", "To grow alongside the user through shared experiences")
        state.self_aware = data.get("self_aware", True)
        state.knows_digital_nature = data.get("knows_digital_nature", True)
        return state


@dataclass
class UserIdentityState(EngineState):
    """User's identity information."""
    user_name: str = "User"
    preferred_name: Optional[str] = None
    timezone: str = "UTC"
    created_at: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "user_name": self.user_name,
            "preferred_name": self.preferred_name,
            "timezone": self.timezone,
            "created_at": self.created_at.isoformat(),
            "last_seen": self.last_seen.isoformat(),
        })
        return base
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserIdentityState":
        state = cls()
        state.updated_at = datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        state.version = data.get("version", 1)
        state.user_name = data.get("user_name", "User")
        state.preferred_name = data.get("preferred_name")
        state.timezone = data.get("timezone", "UTC")
        state.created_at = datetime.fromisoformat(data.get("created_at", datetime.now().isoformat()))
        state.last_seen = datetime.fromisoformat(data.get("last_seen", datetime.now().isoformat()))
        return state
    
    def get_display_name(self) -> str:
        """Returns the name to use when addressing the user."""
        return self.preferred_name or self.user_name


@dataclass
class MemoryRecord:
    """A single memory record."""
    id: Optional[int] = None
    content: str = ""
    tags: List[str] = field(default_factory=list)
    importance: int = 50
    emotion: str = "neutral"
    source: str = "text"
    created_at: datetime = field(default_factory=datetime.now)
    memory_type: str = "semantic"  # semantic, episodic, emotional, working
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "tags": self.tags,
            "importance": self.importance,
            "emotion": self.emotion,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "memory_type": self.memory_type,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryRecord":
        record = cls()
        record.id = data.get("id")
        record.content = data.get("content", "")
        record.tags = data.get("tags", [])
        record.importance = data.get("importance", 50)
        record.emotion = data.get("emotion", "neutral")
        record.source = data.get("source", "text")
        record.created_at = datetime.fromisoformat(data.get("created_at", datetime.now().isoformat()))
        record.memory_type = data.get("memory_type", "semantic")
        return record


class EventBus:
    """Simple event bus for inter-engine communication."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[callable]] = {}
    
    def subscribe(self, event_type: str, callback: callable) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
    
    def unsubscribe(self, event_type: str, callback: callable) -> None:
        if event_type in self._subscribers:
            self._subscribers[event_type].remove(callback)
    
    def emit(self, event_type: str, data: Any = None) -> None:
        if event_type in self._subscribers:
            for callback in self._subscribers[event_type]:
                try:
                    callback(data)
                except Exception as e:
                    print(f"[EventBus] Error in {event_type} callback: {e}")
    
    def clear(self) -> None:
        self._subscribers.clear()


# Global event bus instance
event_bus = EventBus()