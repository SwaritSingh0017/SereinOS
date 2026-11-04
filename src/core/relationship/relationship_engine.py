"""
Relationship Engine - Tracks the bond between Serein and the user.
Trust, familiarity, attachment developed naturally through interaction.
"""
from src.core.base import BaseEngine, RelationshipState, event_bus
from src.database.database import Database
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import json


class RelationshipEngine(BaseEngine):
    """Manages the relationship between Serein and the user."""
    
    @property
    def engine_name(self) -> str:
        return "relationship"
    
    # Relationship growth parameters
    TRUST_PER_INTERACTION = 1
    FAMILIARITY_PER_INTERACTION = 2
    ATTACHMENT_PER_INTERACTION = 0.5
    
    # Milestone thresholds
    MILESTONES = {
        "first_conversation": {"interactions": 1},
        "first_day": {"days": 1},
        "first_week": {"days": 7},
        "first_month": {"days": 30},
        "trusted_friend": {"trust": 40, "familiarity": 40},
        "close_companion": {"trust": 60, "familiarity": 60, "attachment": 40},
        "deep_bond": {"trust": 80, "familiarity": 80, "attachment": 60},
    }
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._relationship: Optional[RelationshipState] = None
        self._milestones_achieved: List[str] = []
        self._interaction_history: List[Dict] = []
        self._last_interaction: Optional[datetime] = None
    
    def initialize(self) -> None:
        """Load relationship state from database."""
        if not self.db:
            self.db = Database()
        
        rel_row = self.db.get_relationship()
        if rel_row:
            self._relationship = RelationshipState(
                trust=rel_row[1],
                familiarity=rel_row[2],
                attachment=rel_row[3],
                interaction_count=rel_row[4],
                total_conversation_minutes=rel_row[5],
            )
        else:
            self._relationship = RelationshipState()
            self.save_state()
        
        # Load milestones from system events
        events = self.db.get_system_events(event_type="milestone")
        self._milestones_achieved = [e[2] for e in events]
        
        self._initialized = True
        event_bus.emit("relationship.initialized", {
            "state": self._relationship.to_dict(),
            "milestones": self._milestones_achieved
        })
    
    def save_state(self) -> None:
        """Persist relationship state to database."""
        if self._relationship and self.db:
            self.db.update_relationship(
                trust=self._relationship.trust,
                familiarity=self._relationship.familiarity,
                attachment=self._relationship.attachment,
                interaction_count=self._relationship.interaction_count,
                total_conversation_minutes=self._relationship.total_conversation_minutes,
            )
    
    def get_state(self) -> RelationshipState:
        return self._relationship or RelationshipState()
    
    def get_relationship(self) -> RelationshipState:
        """Returns current relationship state."""
        return self._relationship or RelationshipState()
    
    def record_interaction(self, duration_minutes: int = 1, 
                          quality: str = "neutral", context: Dict = None) -> Dict[str, int]:
        """
        Records an interaction and updates relationship metrics.
        quality: positive, neutral, negative
        Returns the deltas applied.
        """
        if not self._relationship:
            return {}
        
        now = datetime.now()
        deltas = {}
        
        # Base growth per interaction
        trust_delta = self.TRUST_PER_INTERACTION
        familiarity_delta = self.FAMILIARITY_PER_INTERACTION
        attachment_delta = self.ATTACHMENT_PER_INTERACTION
        
        # Quality modifiers
        if quality == "positive":
            trust_delta *= 2
            attachment_delta *= 2
        elif quality == "negative":
            trust_delta = max(-2, trust_delta // 2)
            attachment_delta = max(-1, attachment_delta // 2)
        
        # Apply deltas
        old_trust = self._relationship.trust
        old_familiarity = self._relationship.familiarity
        old_attachment = self._relationship.attachment
        
        self._relationship.trust = max(0, min(100, self._relationship.trust + trust_delta))
        self._relationship.familiarity = max(0, min(100, self._relationship.familiarity + familiarity_delta))
        self._relationship.attachment = max(0, min(100, self._relationship.attachment + attachment_delta))
        
        deltas["trust"] = self._relationship.trust - old_trust
        deltas["familiarity"] = self._relationship.familiarity - old_familiarity
        deltas["attachment"] = self._relationship.attachment - old_attachment
        
        # Update interaction count and time
        self._relationship.interaction_count += 1
        self._relationship.total_conversation_minutes += duration_minutes
        self._relationship.updated_at = now
        self._last_interaction = now
        
        # Record in history
        self._interaction_history.append({
            "timestamp": now.isoformat(),
            "duration_minutes": duration_minutes,
            "quality": quality,
            "trust": self._relationship.trust,
            "familiarity": self._relationship.familiarity,
            "attachment": self._relationship.attachment,
        })
        
        # Keep last 100 interactions
        self._interaction_history = self._interaction_history[-100:]
        
        # Check milestones
        new_milestones = self._check_milestones()
        
        self.save_state()
        
        event_bus.emit("relationship.updated", {
            "deltas": deltas,
            "new_state": self._relationship.to_dict(),
            "milestones": new_milestones,
            "quality": quality
        })
        
        return deltas
    
    def _check_milestones(self) -> List[str]:
        """Checks and records new milestones."""
        new_milestones = []
        rel = self._relationship
        
        # Time-based milestones
        if self._last_interaction:
            # We'd need first interaction date - for now skip time-based
            pass
        
        # State-based milestones
        milestone_checks = {
            "trusted_friend": rel.trust >= 40 and rel.familiarity >= 40,
            "close_companion": rel.trust >= 60 and rel.familiarity >= 60 and rel.attachment >= 40,
            "deep_bond": rel.trust >= 80 and rel.familiarity >= 80 and rel.attachment >= 60,
        }
        
        for milestone, achieved in milestone_checks.items():
            if achieved and milestone not in self._milestones_achieved:
                self._milestones_achieved.append(milestone)
                new_milestones.append(milestone)
                self._record_milestone(milestone)
        
        return new_milestones
    
    def _record_milestone(self, milestone: str) -> None:
        """Records a milestone in the database."""
        if self.db:
            self.db.add_system_event(
                event_type="milestone",
                description=f"Relationship milestone: {milestone}",
            )
        event_bus.emit("relationship.milestone", {"milestone": milestone})
    
    def get_relationship_context(self) -> str:
        """Returns formatted relationship state for context building."""
        rel = self.get_relationship()
        level = rel.get_relationship_level()
        
        return f"""RELATIONSHIP STATE (0-100):
- Trust: {rel.trust}
- Familiarity: {rel.familiarity}
- Attachment: {rel.attachment}

LEVEL: {level}
INTERACTIONS: {rel.interaction_count}
TOTAL TIME: {rel.total_conversation_minutes} minutes

MILESTONES ACHIEVED: {', '.join(self._milestones_achieved) if self._milestones_achieved else 'None yet'}"""
    
    def get_relationship_influence(self) -> Dict[str, Any]:
        """Returns how relationship should influence behavior."""
        rel = self.get_relationship()
        level = rel.get_relationship_level()
        
        influence = {
            "proactivity": 0,
            "vulnerability": 0,
            "casualness": 0,
            "protectiveness": 0,
            "memory_weight": 1.0,
            "emotional_sync": 0,
        }
        
        if level == "Deep Bond":
            influence = {
                "proactivity": 2,
                "vulnerability": 2,
                "casualness": 2,
                "protectiveness": 2,
                "memory_weight": 1.5,
                "emotional_sync": 2,
            }
        elif level == "Close Companion":
            influence = {
                "proactivity": 1,
                "vulnerability": 1,
                "casualness": 1,
                "protectiveness": 1,
                "memory_weight": 1.3,
                "emotional_sync": 1,
            }
        elif level == "Trusted Friend":
            influence = {
                "proactivity": 1,
                "vulnerability": 0,
                "casualness": 1,
                "protectiveness": 1,
                "memory_weight": 1.1,
                "emotional_sync": 1,
            }
        elif level == "Familiar Presence":
            influence = {
                "proactivity": 0,
                "vulnerability": 0,
                "casualness": 0,
                "protectiveness": 0,
                "memory_weight": 1.0,
                "emotional_sync": 0,
            }
        # New Acquaintance gets defaults
        
        return influence
    
    def get_time_since_last_interaction(self) -> Optional[timedelta]:
        """Returns time since last interaction."""
        if self._last_interaction:
            return datetime.now() - self._last_interaction
        return None
    
    def get_interaction_summary(self, days: int = 30) -> Dict[str, Any]:
        """Returns summary of recent interactions."""
        cutoff = datetime.now() - timedelta(days=days)
        recent = [
            i for i in self._interaction_history
            if datetime.fromisoformat(i["timestamp"]) >= cutoff
        ]
        
        if not recent:
            return {"count": 0, "total_minutes": 0, "avg_quality": "neutral"}
        
        quality_counts = {"positive": 0, "neutral": 0, "negative": 0}
        for i in recent:
            quality_counts[i["quality"]] = quality_counts.get(i["quality"], 0) + 1
        
        return {
            "count": len(recent),
            "total_minutes": sum(i["duration_minutes"] for i in recent),
            "avg_quality": max(quality_counts, key=quality_counts.get),
            "quality_distribution": quality_counts,
        }
    
    def adjust_for_event(self, event_type: str, impact: Dict[str, int]) -> None:
        """
        Adjusts relationship based on significant events.
        impact: dict with trust, familiarity, attachment deltas
        """
        if not self._relationship:
            return
        
        for metric, delta in impact.items():
            if hasattr(self._relationship, metric):
                current = getattr(self._relationship, metric)
                new_value = max(0, min(100, current + delta))
                setattr(self._relationship, metric, new_value)
        
        self._relationship.updated_at = datetime.now()
        self.save_state()
        
        event_bus.emit("relationship.event_adjustment", {
            "event": event_type,
            "impact": impact,
            "new_state": self._relationship.to_dict()
        })
    
    def close(self) -> None:
        """Saves state and cleans up."""
        self.save_state()
        super().close()