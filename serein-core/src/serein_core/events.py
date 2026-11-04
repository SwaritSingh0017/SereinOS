"""
Event bus for inter-component communication within the core service.
Thread-safe, async-first design with typed events.
"""
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, TypeVar, Generic
from uuid import uuid4
import time
import weakref

logger = logging.getLogger("serein.core.events")


@dataclass
class Event:
    """Base event class."""
    type: str
    data: Any = None
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    source: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass
class WakeWordEventData:
    """Payload for CoreEvents.WAKE_WORD_DETECTED."""
    wake_word: str = "Hey Serein"
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    source: str = "voice_engine"
    metadata: dict = field(default_factory=dict)


@dataclass
class UtteranceEventData:
    """Payload for CoreEvents.VOICE_INPUT."""
    text: str = ""
    confidence: float = 1.0
    audio_duration: float = 0.0
    language: str = "en"
    is_final: bool = True
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)



T = TypeVar('T')


class EventBus:
    """Async event bus with typed subscriptions and Pub/Sub patterns."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Event], Any]]] = defaultdict(list)
        self._typed_subscribers: Dict[str, List[Callable[[Any], Any]]] = defaultdict(list)
        self._wildcard_subscribers: List[Callable[[Event], Any]] = []
        self._event_history: List[Event] = []
        self._max_history = 1000
        self._lock = asyncio.Lock()
    
    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        # Store in history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]
        
        # Call typed subscribers
        typed_subs = self._typed_subscribers.get(event.type, [])
        # Call string-based subscribers
        string_subs = self._subscribers.get(event.type, [])
        # Call wildcard subscribers
        wildcard_subs = self._wildcard_subscribers
        
        all_subs = typed_subs + string_subs + wildcard_subs
        
        if not all_subs:
            return
        
        # Execute all subscribers concurrently
        tasks = []
        for sub in all_subs:
            if asyncio.iscoroutinefunction(sub):
                tasks.append(asyncio.create_task(self._safe_call(sub, event)))
            else:
                tasks.append(asyncio.create_task(asyncio.to_thread(self._safe_call_sync, sub, event)))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _safe_call(self, func: Callable, event: Event) -> None:
        try:
            await func(event)
        except Exception as e:
            logger.error(f"Event handler error for {event.type}: {e}", exc_info=True)
    
    def _safe_call_sync(self, func: Callable, event: Event) -> None:
        try:
            func(event)
        except Exception as e:
            logger.error(f"Sync event handler error for {event.type}: {e}", exc_info=True)
    
    def subscribe(self, event_type: str, handler: Callable[[Event], Any]) -> Callable[[], None]:
        """Subscribe to string-based event type."""
        self._subscribers[event_type].append(handler)
        return lambda: self._subscribers[event_type].remove(handler)
    
    def subscribe_typed(self, event_type: str, handler: Callable[[Any], Any]) -> Callable[[], None]:
        """Subscribe with typed data handler."""
        self._typed_subscribers[event_type].append(handler)
        return lambda: self._typed_subscribers[event_type].remove(handler)
    
    def subscribe_all(self, handler: Callable[[Event], Any]) -> Callable[[], None]:
        """Subscribe to all events."""
        self._wildcard_subscribers.append(handler)
        return lambda: self._wildcard_subscribers.remove(handler)
    
    def unsubscribe(self, event_type: str, handler: Callable) -> bool:
        """Unsubscribe from event type."""
        if handler in self._subscribers.get(event_type, []):
            self._subscribers[event_type].remove(handler)
            return True
        return False
    
    def get_history(self, event_type: Optional[str] = None, limit: int = 100) -> List[Event]:
        """Get recent event history."""
        events = self._event_history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]
    
    def clear_history(self) -> None:
        self._event_history.clear()


# Global event bus instance
event_bus = EventBus()


# Core event types
class CoreEvents:
    """Standard core event type constants."""
    
    # Lifecycle
    SERVICE_STARTING = "core.service.starting"
    SERVICE_STARTED = "core.service.started"
    SERVICE_STOPPING = "core.service.stopping"
    SERVICE_STOPPED = "core.service.stopped"
    
    # Database
    DB_CONNECTED = "core.db.connected"
    DB_MIGRATED = "core.db.migrated"
    
    # Identity
    IDENTITY_LOADED = "core.identity.loaded"
    IDENTITY_UPDATED = "core.identity.updated"
    
    # Memory
    MEMORY_STORED = "core.memory.stored"
    MEMORY_RECALLED = "core.memory.recalled"
    MEMORY_CONSOLIDATED = "core.memory.consolidated"
    
    # Emotion
    EMOTION_CHANGED = "core.emotion.changed"
    
    # Relationship
    RELATIONSHIP_UPDATED = "core.relationship.updated"
    
    # Growth
    GROWTH_UPDATED = "core.growth.updated"
    
    # Scheduler
    SCHEDULER_STARTED = "core.scheduler.started"
    EVENT_FIRED = "core.scheduler.event_fired"
    EVENT_SCHEDULED = "core.scheduler.event_scheduled"
    
    # Command Bus
    COMMAND_RECEIVED = "core.command_bus.command_received"
    COMMAND_EXECUTED = "core.command_bus.command_executed"
    COMMAND_FAILED = "core.command_bus.command_failed"
    PERMISSION_DENIED = "core.command_bus.permission_denied"
    
    # Voice
    VOICE_INPUT = "core.voice.input"
    VOICE_OUTPUT = "core.voice.output"
    WAKE_WORD_DETECTED = "core.voice.wake_word"
    
    # Avatar
    AVATAR_EXPRESSION = "core.avatar.expression"
    AVATAR_VISEME = "core.avatar.viseme"
    AVATAR_IDLE = "core.avatar.idle"
    
    # System
    SYSTEM_ACTION = "core.system.action"
    SYSTEM_STATE_CHANGED = "core.system.state_changed"
    
    # Errors
    ERROR = "core.error"


def event_handler(event_type: str):
    """Decorator for event handlers."""
    def decorator(func: Callable[[Event], Any]) -> Callable[[Event], Any]:
        event_bus.subscribe(event_type, func)
        return func
    return decorator


def typed_handler(event_type: str):
    """Decorator for typed event handlers."""
    def decorator(func: Callable[[Any], Any]) -> Callable[[Any], Any]:
        event_bus.subscribe_typed(event_type, func)
        return func
    return decorator