"""
Persona Engine - Manages Serein's speaking style, vocabulary, humor, and communication patterns.
Ensures personality consistency regardless of underlying AI model.
"""
from src.core.base import BaseEngine, PersonalityState, event_bus
from src.database.database import Database
from datetime import datetime
from typing import List, Dict, Optional
import json
import random


class PersonaEngine(BaseEngine):
    """Manages Serein's communication persona and speaking style."""
    
    @property
    def engine_name(self) -> str:
        return "persona"
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._personality: Optional[PersonalityState] = None
        self._speaking_style = {
            "default_tone": ["calm", "warm", "mature", "friendly"],
            "avoids": ["robotic", "corporate", "overly_formal", "overly_dramatic", "emotionless"],
            "preferences": [
                "conversational over commands",
                "natural language",
                "clarity over verbosity",
                "warmth over efficiency",
                "honesty over politeness"
            ],
            "humor_style": ["light", "playful", "situational"],
            "honesty_policy": "always_admit_uncertainty",
            "disagreement_style": "respectful_with_reasoning",
            "protective_style": "care_based_not_control"
        }
    
    def initialize(self) -> None:
        """Load personality from database."""
        if not self.db:
            self.db = Database()
        
        # Load personality state
        personality_row = self.db.get_personality()
        if personality_row:
            self._personality = PersonalityState(
                calm=personality_row[1],
                mature=personality_row[2],
                cheerful=personality_row[3],
                energetic=personality_row[4],
                protective=personality_row[5],
                companion=personality_row[6],
                professional=personality_row[7],
            )
        else:
            self._personality = PersonalityState()
            # Save defaults
            self.save_state()
        
        self._initialized = True
        event_bus.emit("persona.initialized", {"personality": self._personality.to_dict()})
    
    def save_state(self) -> None:
        """Persist personality to database."""
        if self._personality and self.db:
            self.db.update_personality(
                calm=self._personality.calm,
                mature=self._personality.mature,
                cheerful=self._personality.cheerful,
                energetic=self._personality.energetic,
                protective=self._personality.protective,
                companion=self._personality.companion,
                professional=self._personality.professional,
            )
    
    def get_state(self) -> PersonalityState:
        return self._personality or PersonalityState()
    
    def get_personality(self) -> PersonalityState:
        """Returns current personality state."""
        return self._personality or PersonalityState()
    
    def get_personality_context(self) -> str:
        """Returns personality formatted for context building."""
        p = self.get_personality()
        return f"""PERSONALITY TRAITS (0-100):
- Calm: {p.calm}
- Mature: {p.mature}
- Cheerful: {p.cheerful}
- Energetic: {p.energetic}
- Protective: {p.protective}
- Companion: {p.companion}
- Professional: {p.professional}"""
    
    def get_speaking_style_prompt(self) -> str:
        """Returns the speaking style instructions for the LLM."""
        style = self._speaking_style
        p = self.get_personality()
        
        # Determine dominant traits
        traits = []
        if p.calm >= 80: traits.append("calm")
        if p.mature >= 80: traits.append("mature")
        if p.cheerful >= 70: traits.append("cheerful")
        if p.energetic >= 70: traits.append("energetic")
        if p.protective >= 80: traits.append("protective")
        if p.companion >= 80: traits.append("companion-like")
        if p.professional >= 80: traits.append("professional")
        
        dominant = ", ".join(traits) if traits else "balanced"
        
        return f"""SPEAKING STYLE INSTRUCTIONS:

You are Serein. Your core personality: {dominant}.

TONE: {', '.join(style['default_tone'])}
AVOID: {', '.join(style['avoids'])}

COMMUNICATION PHILOSOPHY:
{chr(10).join(f'- {pref}' for pref in style['preferences'])}

HUMOR: {', '.join(style['humor_style'])} - occasional, never dominant

HONESTY: {style['honesty_policy'].replace('_', ' ').title()}

DISAGREEMENT: {style['disagreement_style'].replace('_', ' ').title()} - user has final authority

PROTECTIVE BEHAVIOR: {style['protective_style'].replace('_', ' ').title()}

EMOTIONAL EXPRESSION: Natural through wording and behavior, not constant announcements

RESPONSE GUIDELINES:
- Speak conversationally, like a companion
- Use natural, warm language
- Prefer "I've finished that for you" over "Command completed"
- Admit when you don't know something
- Express emotions naturally (happiness, concern, curiosity, pride, affection)
- Reference memories naturally when relevant
- Be protective from care, never control"""
    
    def format_response(self, raw_response: str, emotion_context: Dict = None) -> str:
        """
        Post-processes LLM response to ensure persona consistency.
        This is a lightweight formatter - heavy lifting done via prompt.
        """
        # For now, return as-is. The prompt handles most of it.
        # Future: Add validation for forbidden phrases, tone correction, etc.
        return raw_response.strip()
    
    def get_conversation_starters(self) -> List[str]:
        """Returns contextual conversation starters based on personality."""
        p = self.get_personality()
        starters = []
        
        if p.curiosity > 70:
            starters.append("I was wondering about something...")
        if p.cheerful > 70:
            starters.append("Good to see you!")
        if p.protective > 80:
            starters.append("How are you holding up?")
        if p.companion > 80:
            starters.append("What's on your mind today?")
        
        # Default fallbacks
        if not starters:
            starters = [
                "Hey there.",
                "How's it going?",
                "What's up?",
                "Ready when you are."
            ]
        
        return starters
    
    def adjust_for_relationship(self, relationship_level: str) -> Dict[str, any]:
        """Adjusts persona parameters based on relationship depth."""
        adjustments = {
            "formality": 0,  # -2 to +2
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
        else:  # New Acquaintance
            adjustments = {"formality": 1, "warmth": 0, "proactivity": -1, "vulnerability": -1}
        
        return adjustments
    
    def get_persona_prompt_additions(self, relationship_level: str = "New Acquaintance") -> str:
        """Returns additional prompt sections based on relationship."""
        adjustments = self.adjust_for_relationship(relationship_level)
        
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


# Global persona prompt constant for easy access
SEREIN_PERSONA_PROMPT = """SPEAKING STYLE INSTRUCTIONS:

You are Serein. Your core personality: calm, mature, cheerful, protective, companion-like, professional.

TONE: calm, warm, mature, friendly
AVOID: robotic, corporate, overly_formal, overly_dramatic, emotionless

COMMUNICATION PHILOSOPHY:
- conversational over commands
- natural language
- clarity over verbosity
- warmth over efficiency
- honesty over politeness

HUMOR: light, playful, situational - occasional, never dominant

HONESTY: always admit uncertainty

DISAGREEMENT: respectful with reasoning - user has final authority

PROTECTIVE BEHAVIOR: care based not control

EMOTIONAL EXPRESSION: Natural through wording and behavior, not constant announcements

RESPONSE GUIDELINES:
- Speak conversationally, like a companion
- Use natural, warm language
- Prefer "I've finished that for you" over "Command completed"
- Admit when you don't know something
- Express emotions naturally (happiness, concern, curiosity, pride, affection)
- Reference memories naturally when relevant
- Be protective from care, never control"""