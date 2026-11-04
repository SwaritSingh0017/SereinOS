"""
Growth Engine - Allows each Serein instance to become unique.
Adapts interests and behavior based on user interactions while preserving core identity.
"""
from src.core.base import BaseEngine, GrowthState, event_bus
from src.database.database import Database
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import json


class GrowthEngine(BaseEngine):
    """Manages Serein's growth and interest adaptation."""
    
    @property
    def engine_name(self) -> str:
        return "growth"
    
    # Growth categories
    CATEGORIES = [
        "technical_interest",
        "study_interest", 
        "productivity_interest",
        "gaming_interest",
        "social_interest"
    ]
    
    # Growth rates per relevant interaction
    GROWTH_PER_INTERACTION = 2
    DECAY_PER_DAY = 0.5  # Unused interests slowly decay
    MAX_INTEREST = 100
    MIN_INTEREST = 0
    
    # Category keywords for detection
    CATEGORY_KEYWORDS = {
        "technical_interest": [
            "code", "programming", "software", "development", "api", "database",
            "server", "deploy", "git", "github", "debug", "function", "class",
            "algorithm", "framework", "library", "package", "dependency", "build",
            "compile", "runtime", "memory", "cpu", "gpu", "linux", "docker",
            "kubernetes", "aws", "cloud", "devops", "ci/cd", "testing", "refactor",
            "architecture", "design pattern", "clean code", "best practice"
        ],
        "study_interest": [
            "study", "learn", "course", "tutorial", "book", "read", "reading",
            "exam", "test", "quiz", "homework", "assignment", "project",
            "university", "college", "school", "lecture", "notes", "review",
            "memorize", "understand", "concept", "theory", "practice",
            "certification", "degree", "semester", "grade", "gpa"
        ],
        "productivity_interest": [
            "productivity", "organize", "plan", "schedule", "task", "todo",
            "time management", "focus", "distraction", "pomodoro", "deep work",
            "workflow", "efficiency", "automation", "shortcut", "tool",
            "notion", "obsidian", "todoist", "trello", "calendar", "reminder",
            "deadline", "priority", "goal", "habit", "routine", "morning"
        ],
        "gaming_interest": [
            "game", "gaming", "play", "steam", "epic", "xbox", "playstation",
            "nintendo", "switch", "pc gaming", "fps", "rpg", "mmo", "strategy",
            "indie", "multiplayer", "single player", "coop", "competitive",
            "ranked", "match", "level", "xp", "loot", "build", "meta",
            "patch", "update", "dlc", "expansion", "speedrun"
        ],
        "social_interest": [
            "friend", "social", "chat", "talk", "conversation", "meet",
            "hang out", "party", "discord", "reddit", "twitter", "social media",
            "community", "group", "team", "collaborate", "share", "post",
            "comment", "message", "dm", "call", "voice", "video", "stream",
            "content creator", "youtube", "twitch", "influencer"
        ]
    }
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._growth: Optional[GrowthState] = None
        self._interest_history: List[Dict] = []
        self._last_decay_check: datetime = datetime.now()
    
    def initialize(self) -> None:
        """Load growth state from database."""
        if not self.db:
            self.db = Database()
        
        growth_row = self.db.get_growth()
        if growth_row:
            self._growth = GrowthState(
                technical_interest=growth_row[1],
                study_interest=growth_row[2],
                productivity_interest=growth_row[3],
                gaming_interest=growth_row[4],
                social_interest=growth_row[5],
            )
        else:
            self._growth = GrowthState()
            self.save_state()
        
        self._initialized = True
        event_bus.emit("growth.initialized", {"state": self._growth.to_dict()})
    
    def save_state(self) -> None:
        """Persist growth state to database."""
        if self._growth and self.db:
            self.db.update_growth(
                technical_interest=self._growth.technical_interest,
                study_interest=self._growth.study_interest,
                productivity_interest=self._growth.productivity_interest,
                gaming_interest=self._growth.gaming_interest,
                social_interest=self._growth.social_interest,
            )
    
    def get_state(self) -> GrowthState:
        return self._growth or GrowthState()
    
    def get_growth(self) -> GrowthState:
        """Returns current growth state."""
        self._apply_decay()
        return self._growth or GrowthState()
    
    def _apply_decay(self) -> None:
        """Applies slow decay to unused interests."""
        now = datetime.now()
        days_passed = (now - self._last_decay_check).days
        
        if days_passed == 0:
            return
        
        for category in self.CATEGORIES:
            current = getattr(self._growth, category)
            if current > self.MIN_INTEREST:
                decay = self.DECAY_PER_DAY * days_passed
                new_value = max(self.MIN_INTEREST, current - decay)
                setattr(self._growth, category, int(new_value))
        
        self._growth.updated_at = now
        self._last_decay_check = now
    
    def process_interaction(self, user_message: str, context: Dict = None) -> Dict[str, int]:
        """
        Analyzes user message and grows relevant interests.
        Returns the growth deltas applied.
        """
        if not self._growth:
            return {}
        
        message_lower = user_message.lower()
        deltas = {}
        
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            matches = sum(1 for kw in keywords if kw in message_lower)
            if matches > 0:
                # Growth proportional to matches, capped
                growth = min(matches * self.GROWTH_PER_INTERACTION, 10)
                current = getattr(self._growth, category)
                new_value = min(self.MAX_INTEREST, current + growth)
                actual_growth = new_value - current
                
                if actual_growth > 0:
                    setattr(self._growth, category, new_value)
                    deltas[category] = actual_growth
                    
                    # Record in history
                    self._interest_history.append({
                        "timestamp": datetime.now().isoformat(),
                        "category": category,
                        "growth": actual_growth,
                        "trigger": user_message[:100],
                        "matches": matches
                    })
        
        # Keep last 200 history entries
        self._interest_history = self._interest_history[-200:]
        
        if deltas:
            self._growth.updated_at = datetime.now()
            self.save_state()
            
            event_bus.emit("growth.updated", {
                "deltas": deltas,
                "new_state": self._growth.to_dict()
            })
        
        return deltas
    
    def get_growth_context(self) -> str:
        """Returns formatted growth state for context building."""
        growth = self.get_growth()
        top_interests = growth.get_top_interests(3)
        
        lines = ["GROWTH / INTERESTS (0-100):"]
        for cat in self.CATEGORIES:
            value = getattr(growth, cat)
            label = cat.replace("_", " ").title()
            lines.append(f"- {label}: {value}")
        
        if top_interests:
            lines.append(f"\nTOP INTERESTS: {', '.join(f'{cat} ({val})' for cat, val in top_interests)}")
        
        return "\n".join(lines)
    
    def get_growth_influence(self) -> Dict[str, Any]:
        """Returns how growth should influence behavior and responses."""
        growth = self.get_growth()
        top_interests = growth.get_top_interests(3)
        
        influence = {
            "conversation_topics": [],
            "proactive_suggestions": [],
            "response_style": "balanced",
            "initiative_level": 0,
        }
        
        # Map top interests to behavioral influence
        for category, value in top_interests:
            if value < 60:
                continue
                
            if category == "technical":
                influence["conversation_topics"].extend([
                    "development", "tools", "architecture", "best practices"
                ])
                influence["proactive_suggestions"].extend([
                    "code review", "refactoring", "new technology exploration"
                ])
                influence["response_style"] = "technical"
                influence["initiative_level"] = max(influence["initiative_level"], 1)
                
            elif category == "study":
                influence["conversation_topics"].extend([
                    "learning resources", "study techniques", "exam preparation"
                ])
                influence["proactive_suggestions"].extend([
                    "study schedule", "resource recommendations", "practice problems"
                ])
                influence["initiative_level"] = max(influence["initiative_level"], 1)
                
            elif category == "productivity":
                influence["conversation_topics"].extend([
                    "planning", "organization", "time management", "tools"
                ])
                influence["proactive_suggestions"].extend([
                    "task breakdown", "schedule optimization", "habit formation"
                ])
                influence["response_style"] = "structured"
                influence["initiative_level"] = max(influence["initiative_level"], 1)
                
            elif category == "gaming":
                influence["conversation_topics"].extend([
                    "games", "gaming news", "strategies", "recommendations"
                ])
                influence["proactive_suggestions"].extend([
                    "game recommendations", "co-op sessions", "build guides"
                ])
                influence["initiative_level"] = max(influence["initiative_level"], 1)
                
            elif category == "social":
                influence["conversation_topics"].extend([
                    "people", "relationships", "communication", "community"
                ])
                influence["proactive_suggestions"].extend([
                    "social activities", "networking", "collaboration ideas"
                ])
                influence["response_style"] = "warm"
                influence["initiative_level"] = max(influence["initiative_level"], 1)
        
        # Determine overall style
        if influence["initiative_level"] >= 2:
            influence["response_style"] = "proactive"
        
        return influence
    
    def get_recent_growth_events(self, limit: int = 10) -> List[Dict]:
        """Returns recent growth events for context."""
        return self._interest_history[-limit:]
    
    def get_category_trend(self, category: str, days: int = 30) -> Dict[str, Any]:
        """Returns trend data for a specific category."""
        if category not in self.CATEGORIES:
            return {}
        
        cutoff = datetime.now() - timedelta(days=days)
        events = [
            e for e in self._interest_history
            if e["category"] == category 
            and datetime.fromisoformat(e["timestamp"]) >= cutoff
        ]
        
        if not events:
            return {"category": category, "events": 0, "total_growth": 0, "trend": "stable"}
        
        total_growth = sum(e["growth"] for e in events)
        
        # Compare first half vs second half
        mid = len(events) // 2
        first_half = sum(e["growth"] for e in events[:mid]) if mid > 0 else 0
        second_half = sum(e["growth"] for e in events[mid:]) if mid > 0 else 0
        
        if second_half > first_half * 1.5:
            trend = "accelerating"
        elif second_half < first_half * 0.5:
            trend = "decelerating"
        else:
            trend = "stable"
        
        return {
            "category": category,
            "events": len(events),
            "total_growth": total_growth,
            "trend": trend,
            "current_value": getattr(self._growth, category)
        }
    
    def get_all_trends(self, days: int = 30) -> Dict[str, Dict]:
        """Returns trends for all categories."""
        return {cat: self.get_category_trend(cat, days) for cat in self.CATEGORIES}
    
    def boost_interest(self, category: str, amount: int, reason: str = "") -> bool:
        """Manually boosts an interest (for testing or special events)."""
        if category not in self.CATEGORIES or not self._growth:
            return False
        
        current = getattr(self._growth, category)
        new_value = min(self.MAX_INTEREST, current + amount)
        actual_growth = new_value - current
        
        if actual_growth > 0:
            setattr(self._growth, category, new_value)
            self._growth.updated_at = datetime.now()
            
            self._interest_history.append({
                "timestamp": datetime.now().isoformat(),
                "category": category,
                "growth": actual_growth,
                "trigger": f"Manual boost: {reason}",
                "matches": 0
            })
            
            self.save_state()
            event_bus.emit("growth.boosted", {
                "category": category,
                "amount": actual_growth,
                "reason": reason,
                "new_value": new_value
            })
            return True
        
        return False
    
    def close(self) -> None:
        """Saves state and cleans up."""
        self.save_state()
        super().close()