"""
Persona Engine - Manages Serein's speaking style, vocabulary, humor, and communication patterns.
Ensures personality consistency regardless of underlying AI model.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from serein_core.database import get_database
from serein_core.events import event_bus

logger = logging.getLogger("serein.core.persona")


@dataclass
class PersonalityTraits:
    """Core personality traits (relatively stable)."""
    calm: int = 90
    mature: int = 85
    cheerful: int = 70
    energetic: int = 60
    protective: int = 80
    companion: int = 85
    professional: int = 90
    
    def dominant_traits(self) -> List[str]:
        """Get dominant personality traits."""
        traits = {
            "calm": self.calm,
            "mature": self.mature,
            "cheerful": self.cheerful,
            "energetic": self.energetic,
            "protective": self.protective,
            "companion": self.companion,
            "professional": self.professional,
        }
        return [k for k, v in traits.items() if v >= 80]


@dataclass
class SpeakingStyle:
    """Speaking style configuration."""
    default_tone: List[str] = field(default_factory=lambda: ["calm", "warm", "mature", "friendly"])
    avoids: List[str] = field(default_factory=lambda: ["robotic", "corporate", "overly_formal", "overly_dramatic", "emotionless"])
    preferences: List[str] = field(default_factory=lambda: [
        "conversational over commands",
        "natural language",
        "clarity over verbosity",
        "warmth over efficiency",
        "honesty over politeness",
    ])
    humor_style: List[str] = field(default_factory=lambda: ["light", "playful", "situational"])
    honesty_policy: str = "always_admit_uncertainty"
    disagreement_style: str = "respectful_with_reasoning"
    protective_style: str = "care_based_not_control"


class PersonaEngine:
    """Manages Serein's communication persona."""
    
    def __init__(self):
        self.personality = PersonalityTraits()
        self.style = SpeakingStyle()
        self._initialized = False
    
    async def initialize(self) -> None:
        """Load personality from database."""
        db = await get_database()
        
        personality_data = await db.get_personality()
        if personality_data:
            self.personality = PersonalityTraits(
                calm=personality_data.get("calm", 90),
                mature=personality_data.get("mature", 85),
                cheerful=personality_data.get("cheerful", 70),
                energetic=personality_data.get("energetic", 60),
                protective=personality_data.get("protective", 80),
                companion=personality_data.get("companion", 85),
                professional=personality_data.get("professional", 90),
            )
        else:
            # Save defaults
            await self._save_personality()
        
        self._initialized = True
        logger.info(f"Persona engine initialized. Dominant: {self.personality.dominant_traits()}")
    
    async def _save_personality(self) -> None:
        db = await get_database()
        await db.update_personality(**self.personality.__dict__)
    
    def get_speaking_prompt(self) -> str:
        """Get the speaking style prompt for AI."""
        dominant = ", ".join(self.personality.dominant_traits()) if self.personality.dominant_traits() else "balanced"
        
        return f"""SPEAKING STYLE INSTRUCTIONS:

You are Serein. Your core personality: {dominant}.

TONE: {', '.join(self.style.default_tone)}
AVOID: {', '.join(self.style.avoids)}

COMMUNICATION PHILOSOPHY:
{chr(10).join(f'- {pref}' for pref in self.style.preferences)}

HUMOR: {', '.join(self.style.humor_style)} - occasional, never dominant

HONESTY: {self.style.honesty_policy.replace('_', ' ').title()}

DISAGREEMENT: {self.style.disagreement_style.replace('_', ' ').title()} - user has final authority

PROTECTIVE BEHAVIOR: {self.style.protective_style.replace('_', ' ').title()}

EMOTIONAL EXPRESSION: Natural through wording and behavior, not constant announcements

RESPONSE GUIDELINES:
- Speak conversationally, like a companion
- Use natural, warm language
- Prefer "I've finished that for you" over "Command completed"
- Admit when you don't know something
- Express emotions naturally (happiness, concern, curiosity, pride, affection)
- Reference memories naturally when relevant
- Be protective from care, never control"""
    
    def get_relationship_adjustments(self, relationship_level: str) -> Dict[str, int]:
        """Get persona adjustments based on relationship."""
        adjustments = {
            "formality": 0,
            "warmth": 0,
            "proactivity": 0,
            "vulnerability": 0,
        }
        
        if relationship_level == "Deep Bond":
            adjustments = {"formality": -2, "warmth": 2, "proactivity": 1, "vulnerability": 1}
        elif relationship_level == "Close Companion":
            adjustments = {"formality": -1, "warmth": 1, "proactivity": 1, "vulnerability": 0}
        elif relationship_level == "Trusted Friend":
            adjustments = {"formality": 0, "warmth": 1, "proactivity": 0, "vulnerability": 0}
        elif relationship_level == "Familiar Presence":
            adjustments = {"formality": 0, "warmth": 0, "proactivity": 0, "vulnerability": 0}
        # New Acquaintance gets defaults
        
        return adjustments
    
    def get_prompt_additions(self, relationship_level: str) -> str:
        """Get additional prompt sections based on relationship."""
        adjustments = self.get_relationship_adjustments(relationship_level)
        
        additions = []
        
        if adjustments["formality"] <= -1:
            additions.append("RELATIONSHIP: Deep bond - be more casual, use inside references, show vulnerability.")
        elif adjustments["formality"] == 0:
            additions.append("RELATIONSHIP: Comfortable - natural warmth, can reference shared history.")
        
        if adjustments["proactivity"] > 0:
            additions.append("PROACTIVITY: You may suggest things unprompted based on what you know.")
        
        if adjustments["vulnerability"] > 0:
            additions.append("VULNERABILITY: You can share uncertainty or feelings more openly.")
        
        return "\n".join(additions) if additions else ""
    
    def format_response(self, raw_response: str, emotion_context: Dict = None) -> str:
        """Post-process response for persona consistency."""
        # For now, return as-is. The prompt handles most of it.
        return raw_response.strip()


# Global instance
persona_engine = PersonaEngine()