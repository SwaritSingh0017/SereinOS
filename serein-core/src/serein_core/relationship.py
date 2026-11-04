"""
Relationship Engine - Tracks the bond between Serein and the user.
Trust, familiarity, attachment developed through interaction.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from serein_core.database import get_database
from serein_core.events import event_bus

logger = logging.getLogger("serein.core.relationship")


@dataclass
class RelationshipState:
    trust: int = 0
    familiarity: int = 0
    attachment: int = 0
    interaction_count: int = 0
    total_conversation_minutes: int = 0
    updated_at: datetime = field(default_factory=datetime.now)
    
    def get_level(self) -> str:
        avg = (self.trust + self.familiarity + self.attachment) / 3
        if avg >= 80: return "Deep Bond"
        elif avg >= 60: return "Close Companion"
        elif avg >= 40: return "Trusted Friend"
        elif avg >= 20: return "Familiar Presence"
        return "New Acquaintance"
    
    def to_dict(self) -> Dict:
        return {
            "trust": self.trust,
            "familiarity": self.familiarity,
            "attachment": self.attachment,
            "interaction_count": self.interaction_count,
            "total_conversation_minutes": self.total_conversation_minutes,
            "level": self.get_level(),
        }


class RelationshipEngine:
    """Manages the relationship bond with the user."""
    
    # Growth per interaction
    TRUST_PER = 1
    FAMILIARITY_PER = 2
    ATTACHMENT_PER = 0.5
    
    # Milestones
    MILESTONES = {
        "first_conversation": {"interactions": 1},
        "trusted_friend": {"trust": 40, "familiarity": 40},
        "close_companion": {"trust": 60, "familiarity": 60, "attachment": 40},
        "deep_bond": {"trust": 80, "familiarity": 80, "attachment": 60},
    }
    
    def __init__(self):
        self.state = RelationshipState()
        self._milestones: List[str] = []
        self._history: List[Dict] = []
        self._last_interaction: Optional[datetime] = None
        self._initialized = False
    
    async def initialize(self) -> None:
        db = await get_database()
        rel_data = await db.get_relationship()
        
        if rel_data:
            self.state = RelationshipState(
                trust=rel_data.get("trust", 0),
                familiarity=rel_data.get("familiarity", 0),
                attachment=rel_data.get("attachment", 0),
                interaction_count=rel_data.get("interaction_count", 0),
                total_conversation_minutes=rel_data.get("total_conversation_minutes", 0),
            )
        else:
            await self._save()
        
        # Load milestones from system events
        events = await db.get_system_events("milestone")
        self._milestones = [e["description"].split(": ")[1] for e in events 
                           if "milestone" in e["description"]]
        
        self._initialized = True
        logger.info(f"Relationship engine initialized. Level: {self.state.get_level()}")
    
    async def _save(self) -> None:
        db = await get_database()
        await db.update_relationship(
            trust=self.state.trust,
            familiarity=self.state.familiarity,
            attachment=self.state.attachment,
            interaction_count=self.state.interaction_count,
            total_conversation_minutes=self.state.total_conversation_minutes,
        )
    
    async def record_interaction(self, duration: int = 1, quality: str = "neutral") -> Dict[str, int]:
        """Record an interaction and update relationship."""
        now = datetime.now()
        deltas = {}
        
        # Quality multipliers
        mult = {"positive": 2, "neutral": 1, "negative": -1}.get(quality, 1)
        
        trust_delta = int(self.TRUST_PER * mult)
        fam_delta = int(self.FAMILIARITY_PER * mult)
        att_delta = int(self.ATTACHMENT_PER * mult)
        
        old = (self.state.trust, self.state.familiarity, self.state.attachment)
        
        self.state.trust = max(0, min(100, self.state.trust + trust_delta))
        self.state.familiarity = max(0, min(100, self.state.familiarity + fam_delta))
        self.state.attachment = max(0, min(100, self.state.attachment + att_delta))
        
        deltas = {
            "trust": self.state.trust - old[0],
            "familiarity": self.state.familiarity - old[1],
            "attachment": self.state.attachment - old[2],
        }
        
        self.state.interaction_count += 1
        self.state.total_conversation_minutes += duration
        self.state.updated_at = now
        self._last_interaction = now
        
        # History
        self._history.append({
            "timestamp": now.isoformat(),
            "duration": duration,
            "quality": quality,
            "trust": self.state.trust,
            "familiarity": self.state.familiarity,
            "attachment": self.state.attachment,
        })
        self._history = self._history[-100:]
        
        # Check milestones
        new_milestones = self._check_milestones()
        
        await self._save()
        
        await event_bus.publish(type("Event", (), {
            "type": "relationship.updated",
            "data": {"deltas": deltas, "state": self.state.to_dict(), "milestones": new_milestones}
        }))
        
        return deltas
    
    def _check_milestones(self) -> List[str]:
        new = []
        rel = self.state
        
        checks = {
            "trusted_friend": rel.trust >= 40 and rel.familiarity >= 40,
            "close_companion": rel.trust >= 60 and rel.familiarity >= 60 and rel.attachment >= 40,
            "deep_bond": rel.trust >= 80 and rel.familiarity >= 80 and rel.attachment >= 60,
        }
        
        for milestone, achieved in checks.items():
            if achieved and milestone not in self._milestones:
                self._milestones.append(milestone)
                new.append(milestone)
                # Log to database
                # Would add system event
        
        return new
    
    async def get_influence(self) -> Dict:
        level = self.state.get_level()
        influence = {
            "proactivity": 0,
            "vulnerability": 0,
            "casualness": 0,
            "protectiveness": 0,
            "memory_weight": 1.0,
            "emotional_sync": 0,
        }
        
        if level == "Deep Bond":
            influence = {"proactivity": 2, "vulnerability": 2, "casualness": 2, 
                        "protectiveness": 2, "memory_weight": 1.5, "emotional_sync": 2}
        elif level == "Close Companion":
            influence = {"proactivity": 1, "vulnerability": 1, "casualness": 1,
                        "protectiveness": 1, "memory_weight": 1.3, "emotional_sync": 1}
        elif level == "Trusted Friend":
            influence = {"proactivity": 1, "vulnerability": 0, "casualness": 1,
                        "protectiveness": 1, "memory_weight": 1.1, "emotional_sync": 1}
        
        return influence
    
    async def get_context(self) -> str:
        return f"""RELATIONSHIP STATE (0-100):
- Trust: {self.state.trust}
- Familiarity: {self.state.familiarity}
- Attachment: {self.state.attachment}

LEVEL: {self.state.get_level()}
INTERACTIONS: {self.state.interaction_count}
TOTAL TIME: {self.state.total_conversation_minutes} minutes

MILESTONES: {', '.join(self._milestones) if self._milestones else 'None yet'}"""
    
    async def time_since_last(self) -> Optional[timedelta]:
        if self._last_interaction:
            return datetime.now() - self._last_interaction
        return None


relationship_engine = RelationshipEngine()