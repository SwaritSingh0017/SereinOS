"""Data contracts for Serein's deterministic performance layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ExpressionCue:
    name: str = "neutral"
    intensity: float = 0.5
    duration: float = 0.35


@dataclass(frozen=True)
class GestureCue:
    name: str = "none"
    intensity: float = 0.0
    duration: float = 0.0


@dataclass(frozen=True)
class GazeCue:
    target: str = "user"
    duration: float = 0.5


@dataclass(frozen=True)
class SpeechCue:
    text: str
    enabled: bool = True
    rate: float = 1.0
    pitch: float = 0.0


@dataclass(frozen=True)
class PerformanceScene:
    speech: SpeechCue
    emotion: str = "neutral"
    emotion_intensity: float = 0.5
    expression: ExpressionCue = field(default_factory=ExpressionCue)
    gesture: GestureCue = field(default_factory=GestureCue)
    gaze: GazeCue = field(default_factory=GazeCue)
    posture: str = "relaxed"
    idle_after_speech: bool = True
    request_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "speech": self.speech.__dict__,
            "emotion": self.emotion,
            "emotion_intensity": self.emotion_intensity,
            "expression": self.expression.__dict__,
            "gesture": self.gesture.__dict__,
            "gaze": self.gaze.__dict__,
            "posture": self.posture,
            "idle_after_speech": self.idle_after_speech,
            "request_id": self.request_id,
        }
