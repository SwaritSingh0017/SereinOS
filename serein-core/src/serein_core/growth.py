"""
Growth Engine - Allows each Serein instance to become unique through interest adaptation.
5 interest categories that evolve based on user interactions while preserving core identity.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from serein_core.database import get_database
from serein_core.events import event_bus

logger = logging.getLogger("serein.core.growth")


@dataclass
class GrowthState:
    """User interest adaptation state."""
    technical_interest: int = 50
    study_interest: int = 50
    productivity_interest: int = 50
    gaming_interest: int = 50
    social_interest: int = 50
    updated_at: datetime = field(default_factory=datetime.now)
    
    CATEGORIES = [
        "technical_interest",
        "study_interest", 
        "productivity_interest",
        "gaming_interest",
        "social_interest",
    ]
    
    def top_interests(self, n: int = 3) -> List[tuple]:
        interests = {k: getattr(self, k) for k in self.CATEGORIES}
        return sorted(interests.items(), key=lambda x: x[1], reverse=True)[:n]
    
    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.CATEGORIES} | {"updated_at": self.updated_at.isoformat()}


class GrowthEngine:
    """Manages Serein's growth and interest adaptation."""
    
    GROWTH_PER = 2
    DECAY_PER_DAY = 0.5
    MAX_INTEREST = 100
    MIN_INTEREST = 0
    
    KEYWORDS = {
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
    
    def __init__(self):
        self.state = GrowthState()
        self._history: List[Dict] = []
        self._last_decay = datetime.now()
        self._initialized = False
    
    async def initialize(self) -> None:
        db = await get_database()
        growth_data = await db.get_growth()
        
        if growth_data:
            self.state = GrowthState(
                technical_interest=growth_data.get("technical_interest", 50),
                study_interest=growth_data.get("study_interest", 50),
                productivity_interest=growth_data.get("productivity_interest", 50),
                gaming_interest=growth_data.get("gaming_interest", 50),
                social_interest=growth_data.get("social_interest", 50),
            )
        else:
            await self._save()
        
        self._initialized = True
        logger.info(f"Growth engine initialized. Top: {self.state.top_interests()}")
    
    async def _save(self) -> None:
        db = await get_database()
        await db.update_growth(**{k: getattr(self.state, k) for k in self.state.CATEGORIES})
    
    async def _apply_decay(self) -> None:
        now = datetime.now()
        days = (now - self._last_decay).days
        
        if days == 0:
            return
        
        for cat in self.state.CATEGORIES:
            current = getattr(self.state, cat)
            if current > self.MIN_INTEREST:
                decay = self.DECAY_PER_DAY * days
                setattr(self.state, cat, max(self.MIN_INTEREST, int(current - decay)))
        
        self.state.updated_at = now
        self._last_decay = now
    
    async def process(self, message: str, context: Dict = None) -> Dict[str, int]:
        """Process message and grow relevant interests."""
        await self._apply_decay()
        
        msg_lower = message.lower()
        deltas = {}
        
        for category, keywords in self.KEYWORDS.items():
            matches = sum(1 for kw in keywords if kw in msg_lower)
            if matches > 0:
                growth = min(matches * self.GROWTH_PER, 10)
                current = getattr(self.state, category)
                new_val = min(self.MAX_INTEREST, current + growth)
                actual = new_val - current
                
                if actual > 0:
                    setattr(self.state, category, new_val)
                    deltas[category] = actual
                    
                    self._history.append({
                        "timestamp": datetime.now().isoformat(),
                        "category": category,
                        "growth": actual,
                        "trigger": message[:100],
                        "matches": matches,
                    })
        
        self._history = self._history[-200:]
        
        if deltas:
            self.state.updated_at = datetime.now()
            await self._save()
            
            await event_bus.publish(type("Event", (), {
                "type": "growth.updated",
                "data": {"deltas": deltas, "state": self.state.to_dict()}
            })())
        
        return deltas
    
    async def get_influence(self) -> Dict:
        """Get how growth should influence behavior."""
        growth = self.state
        top = growth.top_interests(3)
        
        influence = {
            "topics": [],
            "suggestions": [],
            "style": "balanced",
            "initiative": 0,
        }
        
        for cat, val in top:
            if val < 60:
                continue
            
            if cat == "technical_interest":
                influence["topics"].extend(["development", "tools", "architecture", "best practices"])
                influence["suggestions"].extend(["code review", "refactoring", "new tech exploration"])
                influence["style"] = "technical"
                influence["initiative"] = max(influence["initiative"], 1)
            elif cat == "study_interest":
                influence["topics"].extend(["learning", "study techniques", "exam prep"])
                influence["suggestions"].extend(["study schedule", "resources", "practice problems"])
                influence["initiative"] = max(influence["initiative"], 1)
            elif cat == "productivity_interest":
                influence["topics"].extend(["planning", "organization", "time management"])
                influence["suggestions"].extend(["task breakdown", "schedule optimization", "habits"])
                influence["style"] = "structured"
                influence["initiative"] = max(influence["initiative"], 1)
            elif cat == "gaming_interest":
                influence["topics"].extend(["games", "strategies", "recommendations"])
                influence["suggestions"].extend(["game recs", "co-op", "build guides"])
                influence["initiative"] = max(influence["initiative"], 1)
            elif cat == "social_interest":
                influence["topics"].extend(["community", "collaboration", "communication"])
                influence["suggestions"].extend(["social activities", "networking"])
                influence["style"] = "warm"
                influence["initiative"] = max(influence["initiative"], 1)
        
        return influence
    
    async def get_context(self) -> str:
        lines = ["GROWTH / INTERESTS (0-100):"]
        for cat in self.state.CATEGORIES:
            val = getattr(self.state, cat)
            label = cat.replace("_", " ").title()
            lines.append(f"- {label}: {val}")
        
        top = self.state.top_interests(3)
        if top:
            lines.append(f"\nTOP: {', '.join(f'{cat} ({val})' for cat, val in top)}")
        
        return "\n".join(lines)
    
    async def get_trends(self, days: int = 30) -> Dict:
        """Get growth trends for all categories."""
        cutoff = datetime.now() - timedelta(days=days)
        trends = {}
        
        for cat in self.state.CATEGORIES:
            events = [e for e in self._history 
                     if e["category"] == cat and datetime.fromisoformat(e["timestamp"]) >= cutoff]
            
            if not events:
                trends[cat] = {"trend": "stable", "events": 0, "growth": 0, "current": getattr(self.state, cat)}
                continue
            
            total = sum(e["growth"] for e in events)
            mid = len(events) // 2
            first = sum(e["growth"] for e in events[:mid]) if mid > 0 else 0
            second = sum(e["growth"] for e in events[mid:]) if mid > 0 else 0
            
            if second > first * 1.5:
                trend = "accelerating"
            elif second < first * 0.5:
                trend = "decelerating"
            else:
                trend = "stable"
            
            trends[cat] = {
                "trend": trend,
                "events": len(events),
                "growth": total,
                "current": getattr(self.state, cat),
            }
        
        return trends


growth_engine = GrowthEngine()