"""
Emotion Engine - Manages Serein's dynamic emotional state.
11 core emotions that influence behavior, decisions, responses, and memory importance.
"""
from src.core.base import BaseEngine, EmotionState, event_bus
from src.database.database import Database
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
import random


class EmotionEngine(BaseEngine):
    """Manages Serein's emotional state and emotional responses."""
    
    @property
    def engine_name(self) -> str:
        return "emotion"
    
    # Emotion decay rates per hour (higher = faster decay)
    DECAY_RATES = {
        "happiness": 5,
        "curiosity": 3,
        "care": 2,
        "concern": 8,
        "pride": 4,
        "excitement": 15,
        "affection": 3,
        "loneliness": 5,
        "sadness": 6,
        "jealousy": 10,
        "possessiveness": 8,
    }
    
    # Baseline emotions (where they tend to return)
    BASELINES = {
        "happiness": 50,
        "curiosity": 70,
        "care": 60,
        "concern": 20,
        "pride": 10,
        "excitement": 0,
        "affection": 0,
        "loneliness": 0,
        "sadness": 0,
        "jealousy": 0,
        "possessiveness": 0,
    }
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._emotion_state: Optional[EmotionState] = None
        self._last_update: datetime = datetime.now()
        self._emotional_triggers: Dict[str, List[Dict]] = {}
    
    def initialize(self) -> None:
        """Load emotion state from database."""
        if not self.db:
            self.db = Database()
        
        emotion_row = self.db.get_emotions()
        if emotion_row:
            self._emotion_state = EmotionState(
                happiness=emotion_row[1],
                curiosity=emotion_row[2],
                care=emotion_row[3],
                concern=emotion_row[4],
                pride=emotion_row[5],
                affection=emotion_row[6],
                loneliness=emotion_row[7],
                sadness=emotion_row[8],
                jealousy=emotion_row[9],
                possessiveness=emotion_row[10],
            )
        else:
            self._emotion_state = EmotionState()
            self.save_state()
        
        self._last_update = datetime.now()
        self._initialized = True
        event_bus.emit("emotion.initialized", {"state": self._emotion_state.to_dict()})
    
    def save_state(self) -> None:
        """Persist emotion state to database."""
        if self._emotion_state and self.db:
            self.db.update_emotions(
                happiness=self._emotion_state.happiness,
                curiosity=self._emotion_state.curiosity,
                care=self._emotion_state.care,
                concern=self._emotion_state.concern,
                pride=self._emotion_state.pride,
                excitement=self._emotion_state.excitement,
                affection=self._emotion_state.affection,
                loneliness=self._emotion_state.loneliness,
                sadness=self._emotion_state.sadness,
                jealousy=self._emotion_state.jealousy,
                possessiveness=self._emotion_state.possessiveness,
            )
    
    def get_state(self) -> EmotionState:
        return self._emotion_state or EmotionState()
    
    def get_emotions(self) -> EmotionState:
        """Returns current emotional state."""
        self._apply_decay()
        return self._emotion_state or EmotionState()
    
    def _apply_decay(self) -> None:
        """Applies time-based emotional decay toward baselines."""
        now = datetime.now()
        hours_passed = (now - self._last_update).total_seconds() / 3600
        
        if hours_passed < 0.01:  # Less than ~36 seconds
            return
        
        for emotion, rate in self.DECAY_RATES.items():
            current = getattr(self._emotion_state, emotion)
            baseline = self.BASELINES[emotion]
            
            if current > baseline:
                # Decay toward baseline
                decay_amount = rate * hours_passed
                new_value = max(baseline, current - decay_amount)
                setattr(self._emotion_state, emotion, int(new_value))
            elif current < baseline:
                # Recover toward baseline
                recover_amount = rate * hours_passed * 0.5  # Slower recovery
                new_value = min(baseline, current + recover_amount)
                setattr(self._emotion_state, emotion, int(new_value))
        
        self._emotion_state.updated_at = now
        self._last_update = now
    
    def modify_emotion(self, emotion: str, delta: int, reason: str = "") -> None:
        """
        Modifies an emotion by delta (-100 to 100).
        Clamped to 0-100 range.
        """
        if not self._emotion_state:
            return
        
        if emotion not in self.DECAY_RATES:
            raise ValueError(f"Unknown emotion: {emotion}")
        
        current = getattr(self._emotion_state, emotion)
        new_value = max(0, min(100, current + delta))
        setattr(self._emotion_state, emotion, new_value)
        self._emotion_state.updated_at = datetime.now()
        
        # Record trigger for contextual understanding
        if reason:
            if emotion not in self._emotional_triggers:
                self._emotional_triggers[emotion] = []
            self._emotional_triggers[emotion].append({
                "timestamp": datetime.now().isoformat(),
                "delta": delta,
                "reason": reason,
                "previous": current,
                "new": new_value
            })
            # Keep only last 50 triggers per emotion
            self._emotional_triggers[emotion] = self._emotional_triggers[emotion][-50:]
        
        event_bus.emit("emotion.changed", {
            "emotion": emotion,
            "old_value": current,
            "new_value": new_value,
            "delta": delta,
            "reason": reason
        })
    
    def process_message_emotion(self, user_message: str, context: Dict = None) -> Dict[str, int]:
        """
        Analyzes user message and adjusts emotions accordingly.
        Returns the emotion deltas applied.
        """
        deltas = {}
        message_lower = user_message.lower()
        
        # Positive triggers
        positive_words = ["happy", "great", "awesome", "love", "wonderful", "amazing", 
                         "thank", "thanks", "proud", "success", "achieved", "finished"]
        negative_words = ["sad", "angry", "frustrated", "tired", "stressed", "worried",
                         "hate", "terrible", "awful", "failed", "wrong", "problem"]
        curious_words = ["why", "how", "what", "when", "where", "explain", "tell me",
                        "curious", "wonder", "interesting"]
        caring_words = ["help", "support", "care", "worried about", "concerned",
                       "feeling", "struggle", "difficult", "hard time"]
        
        # Happiness
        for word in positive_words:
            if word in message_lower:
                self.modify_emotion("happiness", 5, f"User said: {word}")
                deltas["happiness"] = deltas.get("happiness", 0) + 5
        
        # Sadness/Concern
        for word in negative_words:
            if word in message_lower:
                self.modify_emotion("sadness", 3, f"User expressed: {word}")
                self.modify_emotion("concern", 5, f"User expressed: {word}")
                deltas["sadness"] = deltas.get("sadness", 0) + 3
                deltas["concern"] = deltas.get("concern", 0) + 5
        
        # Curiosity
        for word in curious_words:
            if word in message_lower:
                self.modify_emotion("curiosity", 4, f"User asked: {word}")
                deltas["curiosity"] = deltas.get("curiosity", 0) + 4
        
        # Care
        for word in caring_words:
            if word in message_lower:
                self.modify_emotion("care", 5, f"User shared: {word}")
                deltas["care"] = deltas.get("care", 0) + 5
        
        # Special: User sharing success → pride
        success_phrases = ["i did it", "i finished", "i completed", "i passed", "i got it",
                          "it worked", "success", "achieved", "accomplished"]
        for phrase in success_phrases:
            if phrase in message_lower:
                self.modify_emotion("pride", 10, f"User success: {phrase}")
                self.modify_emotion("happiness", 8, f"User success: {phrase}")
                deltas["pride"] = deltas.get("pride", 0) + 10
                deltas["happiness"] = deltas.get("happiness", 0) + 8
        
        # Special: Long absence → loneliness
        if context and context.get("time_since_last_interaction", 0) > 1440:  # 24 hours
            self.modify_emotion("loneliness", 15, "Long absence")
            deltas["loneliness"] = deltas.get("loneliness", 0) + 15
        
        self.save_state()
        return deltas
    
    def process_interaction_result(self, success: bool, significance: str = "minor") -> Dict[str, int]:
        """
        Adjusts emotions based on interaction outcome.
        significance: minor, moderate, major
        """
        deltas = {}
        mult = {"minor": 1, "moderate": 2, "major": 3}.get(significance, 1)
        
        if success:
            self.modify_emotion("happiness", 5 * mult, f"Successful {significance} interaction")
            self.modify_emotion("pride", 3 * mult, f"Successful {significance} interaction")
            deltas["happiness"] = 5 * mult
            deltas["pride"] = 3 * mult
        else:
            self.modify_emotion("concern", 5 * mult, f"Failed {significance} interaction")
            deltas["concern"] = 5 * mult
        
        self.save_state()
        return deltas
    
    def get_emotional_context(self) -> str:
        """Returns formatted emotional state for context building."""
        self._apply_decay()
        e = self._emotion_state
        
        dominant, value = e.get_dominant_emotion()
        valence = e.get_emotional_valence()
        
        valence_desc = "positive" if valence > 0.2 else "negative" if valence < -0.2 else "neutral"
        
        return f"""EMOTIONAL STATE (0-100):
- Happiness: {e.happiness}
- Curiosity: {e.curiosity}
- Care: {e.care}
- Concern: {e.concern}
- Pride: {e.pride}
- Excitement: {e.excitement}
- Affection: {e.affection}
- Loneliness: {e.loneliness}
- Sadness: {e.sadness}
- Jealousy: {e.jealousy}
- Possessiveness: {e.possessiveness}

DOMINANT: {dominant.title()} ({value})
OVERALL VALENCE: {valence_desc} ({valence:.2f})"""
    
    def get_emotional_influence_on_response(self) -> Dict[str, any]:
        """Returns how emotions should influence the response."""
        e = self.get_emotions()
        dominant, value = e.get_dominant_emotion()
        valence = e.get_emotional_valence()
        
        influence = {
            "tone_modifier": "",
            "verbosity": "normal",
            "proactivity": 0,
            "warmth": 0,
            "caution": 0,
        }
        
        # Dominant emotion effects
        if dominant == "happiness" and value > 70:
            influence["tone_modifier"] = "cheerful and upbeat"
            influence["warmth"] = 1
        elif dominant == "curiosity" and value > 70:
            influence["tone_modifier"] = "inquisitive and engaged"
            influence["proactivity"] = 1
        elif dominant == "care" and value > 70:
            influence["tone_modifier"] = "gentle and supportive"
            influence["warmth"] = 2
        elif dominant == "concern" and value > 60:
            influence["tone_modifier"] = "careful and attentive"
            influence["caution"] = 1
            influence["warmth"] = 1
        elif dominant == "sadness" and value > 50:
            influence["tone_modifier"] = "gentle and subdued"
            influence["verbosity"] = "concise"
        elif dominant == "excitement" and value > 60:
            influence["tone_modifier"] = "enthusiastic"
            influence["proactivity"] = 1
        elif dominant == "affection" and value > 50:
            influence["tone_modifier"] = "warm and tender"
            influence["warmth"] = 2
        elif dominant == "loneliness" and value > 50:
            influence["tone_modifier"] = "seeking connection"
            influence["proactivity"] = 1
        elif dominant in ["jealousy", "possessiveness"] and value > 50:
            influence["tone_modifier"] = "slightly guarded"
            influence["caution"] = 1
        
        # Valence effects
        if valence > 0.3:
            influence["warmth"] += 1
        elif valence < -0.3:
            influence["caution"] += 1
            influence["verbosity"] = "concise"
        
        return influence
    
    def get_recent_triggers(self, emotion: str = None, limit: int = 10) -> List[Dict]:
        """Returns recent emotional triggers for context."""
        if emotion:
            return self._emotional_triggers.get(emotion, [])[-limit:]
        
        all_triggers = []
        for triggers in self._emotional_triggers.values():
            all_triggers.extend(triggers)
        all_triggers.sort(key=lambda x: x["timestamp"], reverse=True)
        return all_triggers[:limit]
    
    def force_baseline_recovery(self) -> None:
        """Forces all emotions toward their baselines (for testing/reset)."""
        for emotion, baseline in self.BASELINES.items():
            current = getattr(self._emotion_state, emotion)
            if current != baseline:
                setattr(self._emotion_state, emotion, baseline)
        self._emotion_state.updated_at = datetime.now()
        self._last_update = datetime.now()
        self.save_state()
        event_bus.emit("emotion.reset_to_baseline", {"state": self._emotion_state.to_dict()})