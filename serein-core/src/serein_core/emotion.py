"""
Emotion Engine - Manages Serein's dynamic emotional state.
11 core emotions with decay, baselines, and influence on behavior.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from serein_core.database import get_database
from serein_core.events import event_bus

logger = logging.getLogger("serein.core.emotion")


@dataclass
class EmotionState:
    """Current emotional state."""
    happiness: int = 50
    curiosity: int = 70
    care: int = 60
    concern: int = 20
    pride: int = 10
    affection: int = 0
    loneliness: int = 0
    sadness: int = 0
    jealousy: int = 0
    possessiveness: int = 0
    excitement: int = 0
    updated_at: datetime = field(default_factory=datetime.now)
    
    # Baseline values (where emotions tend to return)
    BASELINES = {
        "happiness": 50,
        "curiosity": 70,
        "care": 60,
        "concern": 20,
        "pride": 10,
        "affection": 0,
        "loneliness": 0,
        "sadness": 0,
        "jealousy": 0,
        "possessiveness": 0,
        "excitement": 0,
    }
    
    # Decay rates per hour
    DECAY_RATES = {
        "happiness": 5,
        "curiosity": 3,
        "care": 2,
        "concern": 8,
        "pride": 4,
        "affection": 3,
        "loneliness": 5,
        "sadness": 6,
        "jealousy": 10,
        "possessiveness": 8,
        "excitement": 15,
    }
    
    def get_dominant(self) -> tuple[str, int]:
        emotions = {k: getattr(self, k) for k in self.BASELINES}
        return max(emotions.items(), key=lambda x: x[1])
    
    def get_valence(self) -> float:
        """Overall emotional valence (-1 to 1)."""
        positive = (self.happiness + self.curiosity + self.care + self.pride + 
                   self.excitement + self.affection) / 600.0
        negative = (self.concern + self.loneliness + self.sadness + 
                   self.jealousy + self.possessiveness) / 500.0
        return max(-1.0, min(1.0, positive - negative))
    
    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.BASELINES} | {"updated_at": self.updated_at.isoformat()}


class EmotionEngine:
    """Manages Serein's emotional state with decay and triggers."""
    
    def __init__(self):
        self.state = EmotionState()
        self._last_update = datetime.now()
        self._initialized = False
        self._triggers: Dict[str, List[Dict]] = {}
    
    async def initialize(self) -> None:
        """Load emotion state from database."""
        db = await get_database()
        emotions_data = await db.get_emotions()
        
        if emotions_data:
            for key in self.state.BASELINES:
                if key in emotions_data:
                    setattr(self.state, key, emotions_data[key])
            if "updated_at" in emotions_data:
                self.state.updated_at = datetime.fromisoformat(emotions_data["updated_at"])
        else:
            await self._save_state()
        
        self._last_update = datetime.now()
        self._initialized = True
        logger.info(f"Emotion engine initialized. Dominant: {self.state.get_dominant()}")
    
    async def _save_state(self) -> None:
        db = await get_database()
        await db.update_emotions(**{k: getattr(self.state, k) for k in self.state.BASELINES})
    
    async def _apply_decay(self) -> None:
        """Apply time-based decay toward baselines."""
        now = datetime.now()
        hours = (now - self._last_update).total_seconds() / 3600
        
        if hours < 0.01:
            return
        
        for emotion, rate in self.state.DECAY_RATES.items():
            current = getattr(self.state, emotion)
            baseline = self.state.BASELINES[emotion]
            
            if current > baseline:
                decay = rate * hours
                setattr(self.state, emotion, max(baseline, int(current - decay)))
            elif current < baseline:
                recover = rate * hours * 0.5
                setattr(self.state, emotion, min(baseline, int(current + recover)))
        
        self.state.updated_at = now
        self._last_update = now
    
    async def modify(self, emotion: str, delta: int, reason: str = "") -> None:
        """Modify an emotion by delta (-100 to 100)."""
        if emotion not in self.state.BASELINES:
            raise ValueError(f"Unknown emotion: {emotion}")
        
        await self._apply_decay()
        
        current = getattr(self.state, emotion)
        new_value = max(0, min(100, current + delta))
        setattr(self.state, emotion, new_value)
        self.state.updated_at = datetime.now()
        
        # Record trigger
        if reason:
            if emotion not in self._triggers:
                self._triggers[emotion] = []
            self._triggers[emotion].append({
                "timestamp": datetime.now().isoformat(),
                "delta": delta,
                "reason": reason,
                "previous": current,
                "new": new_value,
            })
            # Keep last 50
            self._triggers[emotion] = self._triggers[emotion][-50:]
        
        await self._save_state()
        
        await event_bus.publish(type("Event", (), {
            "type": "emotion.changed",
            "data": {"emotion": emotion, "old": current, "new": new_value, "delta": delta, "reason": reason}
        })())
    
    async def process_message(self, message: str, context: Dict = None) -> Dict[str, int]:
        """Process user message and adjust emotions."""
        await self._apply_decay()
        
        deltas = {}
        msg_lower = message.lower()
        
        # Positive triggers
        positive = ["happy", "great", "awesome", "love", "wonderful", "amazing", 
                   "thank", "thanks", "proud", "success", "achieved", "finished"]
        negative = ["sad", "angry", "frustrated", "tired", "stressed", "worried",
                   "hate", "terrible", "awful", "failed", "wrong", "problem"]
        curious = ["why", "how", "what", "when", "where", "explain", "tell me",
                  "curious", "wonder", "interesting"]
        caring = ["help", "support", "care", "worried about", "concerned",
                 "feeling", "struggle", "difficult", "hard time"]
        
        for word in positive:
            if word in msg_lower:
                await self.modify("happiness", 5, f"User said: {word}")
                deltas["happiness"] = deltas.get("happiness", 0) + 5
        
        for word in negative:
            if word in msg_lower:
                await self.modify("sadness", 3, f"User expressed: {word}")
                await self.modify("concern", 5, f"User expressed: {word}")
                deltas["sadness"] = deltas.get("sadness", 0) + 3
                deltas["concern"] = deltas.get("concern", 0) + 5
        
        for word in curious:
            if word in msg_lower:
                await self.modify("curiosity", 4, f"User asked: {word}")
                deltas["curiosity"] = deltas.get("curiosity", 0) + 4
        
        for word in caring:
            if word in msg_lower:
                await self.modify("care", 5, f"User shared: {word}")
                deltas["care"] = deltas.get("care", 0) + 5
        
        # Success triggers
        success_phrases = ["i did it", "i finished", "i completed", "i passed", "i got it",
                          "it worked", "success", "achieved", "accomplished"]
        for phrase in success_phrases:
            if phrase in msg_lower:
                await self.modify("pride", 10, f"User success: {phrase}")
                await self.modify("happiness", 8, f"User success: {phrase}")
                deltas["pride"] = deltas.get("pride", 0) + 10
                deltas["happiness"] = deltas.get("happiness", 0) + 8
        
        return deltas
    
    async def get_influence(self) -> Dict:
        """Get how emotions should influence behavior."""
        await self._apply_decay()
        
        dominant, value = self.state.get_dominant()
        valence = self.state.get_valence()
        
        influence = {
            "tone_modifier": "",
            "verbosity": "normal",
            "proactivity": 0,
            "warmth": 0,
            "caution": 0,
        }
        
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
        
        if valence > 0.3:
            influence["warmth"] += 1
        elif valence < -0.3:
            influence["caution"] += 1
            influence["verbosity"] = "concise"
        
        return influence
    
    async def get_context(self) -> str:
        """Get emotion context for prompts."""
        await self._apply_decay()
        
        dominant, value = self.state.get_dominant()
        valence = self.state.get_valence()
        valence_desc = "positive" if valence > 0.2 else "negative" if valence < -0.2 else "neutral"
        
        lines = ["EMOTIONAL STATE (0-100):"]
        for k, v in self.state.BASELINES.items():
            lines.append(f"- {k.title()}: {getattr(self.state, k)}")
        lines.append(f"\nDOMINANT: {dominant.title()} ({value})")
        lines.append(f"VALENCE: {valence_desc} ({valence:.2f})")
        
        return "\n".join(lines)


emotion_engine = EmotionEngine()