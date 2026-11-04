"""Stable data contracts for Serein's intent layer.

The intent layer describes *what the user wants*. It does not execute the
requested action. Execution belongs to Decision/Command Bus layers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class IntentKind(str, Enum):
    GREETING = "greeting"
    FAREWELL = "farewell"
    CONVERSATION = "conversation"
    QUESTION = "question"
    REMINDER = "reminder"
    SYSTEM_CONTROL = "system_control"
    FILE_OPERATION = "file_operation"
    APP_CONTROL = "app_control"
    SEARCH = "search"
    INFORMATION = "information"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentSchema:
    name: str
    description: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Entity:
    """A normalized piece of information extracted from the utterance."""

    name: str
    value: Any
    source: str = "rule"
    confidence: float = 1.0


@dataclass(frozen=True)
class IntentResult:
    """Canonical output consumed by Decision Engine (#4)."""

    intent: str
    confidence: float
    entities: Mapping[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    raw_text: str = ""
    normalized_text: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "entities", dict(self.entities))

    @property
    def kind(self) -> IntentKind:
        try:
            return IntentKind(self.intent)
        except ValueError:
            return IntentKind.UNKNOWN

    @property
    def is_action(self) -> bool:
        return self.intent in {
            IntentKind.REMINDER.value,
            IntentKind.SYSTEM_CONTROL.value,
            IntentKind.FILE_OPERATION.value,
            IntentKind.APP_CONTROL.value,
        }


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
