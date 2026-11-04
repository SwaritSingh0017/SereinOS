"""Deterministic translation from semantic responses to avatar performance.

The language model supplies semantic meaning; this engine owns animation choices.
That separation keeps Unity behavior predictable and prevents an LLM from
micromanaging animation state.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import ExpressionCue, GestureCue, GazeCue, PerformanceScene, SpeechCue


class PerformanceEngine:
    EXPRESSION_MAP = {
        "happy": "gentle_smile",
        "excited": "bright_smile",
        "sad": "soft_sad",
        "angry": "firm",
        "fear": "concerned",
        "surprised": "surprised",
        "curious": "curious",
        "care": "warm",
        "concern": "concerned",
        "neutral": "neutral",
    }

    GESTURE_MAP = {
        "greeting": "small_wave",
        "happy": "open_hands",
        "excited": "enthusiastic",
        "sad": "small_nod",
        "concern": "gentle_reassure",
        "care": "gentle_reassure",
        "surprised": "quick_reaction",
        "curious": "head_tilt",
        "neutral": "none",
    }

    POSTURE_MAP = {
        "angry": "firm",
        "sad": "soft",
        "excited": "energetic",
        "happy": "relaxed",
        "neutral": "relaxed",
        "concern": "attentive",
        "care": "attentive",
    }

    def compose(
        self,
        text: str,
        *,
        emotion: str = "neutral",
        intensity: float = 0.5,
        intent: str = "respond",
        gaze: str = "user",
        request_id: Optional[str] = None,
    ) -> PerformanceScene:
        emotion = emotion.lower().strip() or "neutral"
        intensity = max(0.0, min(1.0, float(intensity)))
        intent = intent.lower().strip()

        gesture_name = self.GESTURE_MAP.get(emotion, "none")
        if intent in {"greet", "greeting", "hello"}:
            gesture_name = "small_wave"
        elif intent in {"farewell", "goodbye"}:
            gesture_name = "small_wave"
        elif intent in {"confirm", "acknowledge"}:
            gesture_name = "small_nod"

        if intent == "thinking":
            gesture_name = "thinking_pose"
        if not text.strip():
            gesture_name = "none"

        expression = self.EXPRESSION_MAP.get(emotion, "neutral")
        gesture_intensity = min(1.0, intensity + 0.15) if gesture_name != "none" else 0.0

        return PerformanceScene(
            speech=SpeechCue(text=text),
            emotion=emotion,
            emotion_intensity=intensity,
            expression=ExpressionCue(expression, intensity, 0.35),
            gesture=GestureCue(gesture_name, gesture_intensity, 0.8 if gesture_name != "none" else 0.0),
            gaze=GazeCue(gaze or "user", 0.5),
            posture=self.POSTURE_MAP.get(emotion, "relaxed"),
            idle_after_speech=True,
            request_id=request_id,
        )

    @staticmethod
    def infer_intent(text: str) -> str:
        lowered = re.sub(r"\s+", " ", text.lower()).strip()
        if re.match(r"^(hi|hello|hey|good morning|good evening)\b", lowered):
            return "greeting"
        if re.match(r"^(bye|goodbye|see you)\b", lowered):
            return "farewell"
        return "respond"
