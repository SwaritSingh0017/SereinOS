"""
Proactive Intelligence Engine - Generalizes the scheduler into ambient noticing/speaking-up behavior.
Monitors context, detects opportunities, and proactively engages the user with guardrails.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Dict, Optional, Callable, Any
from uuid import uuid4

from serein_core.config import get_config as config
from serein_core.database import get_database
from serein_core.events import event_bus
from serein_core.scheduler import get_scheduler, TriggerType

logger = logging.getLogger("serein.core.proactive")


class ProactiveTrigger(Enum):
    """Types of proactive triggers."""
    PATTERN_MATCH = "pattern_match"           # User behavior pattern detected
    CONTEXT_CHANGE = "context_change"         # Environment/system context shift
    TIME_BASED = "time_based"                 # Time of day / routine
    STATE_THRESHOLD = "state_threshold"       # Emotion/relationship/growth threshold
    EXTERNAL_EVENT = "external_event"         # Calendar, weather, notifications
    LEARNING_OPPORTUNITY = "learning_opportunity"  # Detected knowledge gap
    HEALTH_WELLNESS = "health_wellness"       # Break reminder, posture, eye strain


class ProactiveActionType(Enum):
    """Types of proactive actions."""
    SUGGESTION = "suggestion"                 # Gentle suggestion
    REMINDER = "reminder"                     # Time-based reminder
    QUESTION = "question"                     # Engaging question
    OFFER_HELP = "offer_help"                 # Offer to help with task
    SHARE_INSIGHT = "share_insight"           # Share learned pattern
    CHECK_IN = "check_in"                     # Emotional check-in
    CELEBRATE = "celebrate"                   # Acknowledge achievement


@dataclass
class ProactiveRule:
    """A rule that defines when and how to be proactive."""
    id: str
    name: str
    trigger: ProactiveTrigger
    condition: Callable[[], bool]  # Async condition check
    action_type: ProactiveActionType
    message_template: str
    cooldown_minutes: int = 60
    priority: int = 5  # 1-10
    enabled: bool = True
    last_fired: Optional[datetime] = None
    fire_count: int = 0
    max_fires_per_day: int = 3
    
    def can_fire(self) -> bool:
        if not self.enabled:
            return False
        if self.last_fired and datetime.now() - self.last_fired < timedelta(minutes=self.cooldown_minutes):
            return False
        # Check daily limit
        today = datetime.now().date()
        if self.last_fired and self.last_fired.date() == today and self.fire_count >= self.max_fires_per_day:
            return False
        return True


@dataclass
class ProactiveEvent:
    """Record of a proactive action taken."""
    id: str
    rule_id: str
    timestamp: datetime
    action_type: ProactiveActionType
    message: str
    user_response: Optional[str] = None
    accepted: Optional[bool] = None


class ProactiveEngine:
    """
    Proactive Intelligence Engine.
    
    Monitors various signals and proactively engages the user.
    Guardrails ensure it's helpful, not annoying.
    """
    
    def __init__(self):
        self.rules: List[ProactiveRule] = []
        self.history: List[ProactiveEvent] = []
        self._running = False
        self._check_interval = 60  # seconds
        self._task: Optional[asyncio.Task] = None
        self._context_snapshot: Dict[str, Any] = {}
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize proactive engine with built-in rules."""
        self._register_builtin_rules()
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Proactive Intelligence Engine initialized")
        
        await event_bus.publish(type("Event", (), {
            "type": "proactive.initialized",
            "data": {"rules_loaded": len(self.rules)}
        })())
    
    def _register_builtin_rules(self) -> None:
        """Register built-in proactive rules."""
        
        # 1. Break reminder (Health/Wellness)
        self.rules.append(ProactiveRule(
            id="break_reminder",
            name="Break Reminder",
            trigger=ProactiveTrigger.TIME_BASED,
            condition=self._check_break_needed,
            action_type=ProactiveActionType.SUGGESTION,
            message_template="You've been focused for a while. Want to take a short break? I can set a timer.",
            cooldown_minutes=45,
            priority=7,
            max_fires_per_day=6,
        ))
        
        # 2. Late night check-in (Health/Wellness)
        self.rules.append(ProactiveRule(
            id="late_night_checkin",
            name="Late Night Check-in",
            trigger=ProactiveTrigger.TIME_BASED,
            condition=self._check_late_night,
            action_type=ProactiveActionType.CHECK_IN,
            message_template="It's getting late. How are you feeling? Want to wind down?",
            cooldown_minutes=120,
            priority=8,
            max_fires_per_day=1,
        ))
        
        # 3. Morning routine prompt (Pattern Match)
        self.rules.append(ProactiveRule(
            id="morning_routine",
            name="Morning Routine",
            trigger=ProactiveTrigger.PATTERN_MATCH,
            condition=self._check_morning_pattern,
            action_type=ProactiveActionType.QUESTION,
            message_template="Good morning! How did you sleep? Any goals for today?",
            cooldown_minutes=240,
            priority=6,
            max_fires_per_day=1,
        ))
        
        # 4. High emotion check-in (State Threshold)
        self.rules.append(ProactiveRule(
            id="high_stress_checkin",
            name="High Stress Check-in",
            trigger=ProactiveTrigger.STATE_THRESHOLD,
            condition=self._check_high_stress,
            action_type=ProactiveActionType.CHECK_IN,
            message_template="You seem stressed. Want to talk about it or take a breather?",
            cooldown_minutes=60,
            priority=9,
            max_fires_per_day=3,
        ))
        
        # 5. Achievement celebration (State Threshold)
        self.rules.append(ProactiveRule(
            id="celebrate_achievement",
            name="Celebrate Achievement",
            trigger=ProactiveTrigger.STATE_THRESHOLD,
            condition=self._check_achievement,
            action_type=ProactiveActionType.CELEBRATE,
            message_template="That's awesome! 🎉 You've made great progress on {topic}. How does it feel?",
            cooldown_minutes=120,
            priority=7,
            max_fires_per_day=2,
        ))
        
        # 6. Learning opportunity (Learning Opportunity)
        self.rules.append(ProactiveRule(
            id="learning_opportunity",
            name="Learning Opportunity",
            trigger=ProactiveTrigger.LEARNING_OPPORTUNITY,
            condition=self._check_learning_gap,
            action_type=ProactiveActionType.OFFER_HELP,
            message_template="I noticed you're working on {topic}. Want me to find resources or explain anything?",
            cooldown_minutes=180,
            priority=5,
            max_fires_per_day=2,
        ))
        
        # 7. Relationship milestone (State Threshold)
        self.rules.append(ProactiveRule(
            id="relationship_milestone",
            name="Relationship Milestone",
            trigger=ProactiveTrigger.STATE_THRESHOLD,
            condition=self._check_relationship_milestone,
            action_type=ProactiveActionType.SHARE_INSIGHT,
            message_template="We've been talking for a while now. I've learned so much about you - like {detail}. It's been great getting to know you!",
            cooldown_minutes=1440,  # Once per day max
            priority=8,
            max_fires_per_day=1,
        ))
        
        # 8. Context-based suggestion (Context Change)
        self.rules.append(ProactiveRule(
            id="context_suggestion",
            name="Context-aware Suggestion",
            trigger=ProactiveTrigger.CONTEXT_CHANGE,
            condition=self._check_context_opportunity,
            action_type=ProactiveActionType.SUGGESTION,
            message_template="Since you're {context}, maybe {suggestion}?",
            cooldown_minutes=90,
            priority=5,
            max_fires_per_day=3,
        ))
        
        # 9. Idle engagement (Pattern Match)
        self.rules.append(ProactiveRule(
            id="idle_engagement",
            name="Idle Engagement",
            trigger=ProactiveTrigger.PATTERN_MATCH,
            condition=self._check_idle_time,
            action_type=ProactiveActionType.QUESTION,
            message_template="You've been quiet for a bit. Thinking about something, or just relaxing?",
            cooldown_minutes=120,
            priority=4,
            max_fires_per_day=4,
        ))
        
        # 10. Goal progress check (Time Based)
        self.rules.append(ProactiveRule(
            id="goal_progress_check",
            name="Goal Progress Check",
            trigger=ProactiveTrigger.TIME_BASED,
            condition=self._check_goal_progress,
            action_type=ProactiveActionType.QUESTION,
            message_template="How's the {goal} coming along? Need any help?",
            cooldown_minutes=360,
            priority=6,
            max_fires_per_day=2,
        ))
    
    # Condition check methods
    
    async def _check_break_needed(self) -> bool:
        """Check if user needs a break based on session duration and activity."""
        try:
            from ..context import get_context_engine
            context = get_context_engine()
            if hasattr(context, 'get_context'):
                ctx = await context.get_context()
                duration = ctx.session_duration_minutes if hasattr(ctx, 'session_duration_minutes') else 0
                return duration > 90  # 90 minutes
        except Exception:
            pass
        return False
    
    async def _check_late_night(self) -> bool:
        """Check if it's late night (after 11 PM)."""
        hour = datetime.now().hour
        return hour >= 23 or hour < 5
    
    async def _check_morning_pattern(self) -> bool:
        """Check if it's morning and first interaction of the day."""
        hour = datetime.now().hour
        if 6 <= hour <= 10:
            try:
                from serein_core.relationship import get_relationship_engine
                rel = get_relationship_engine()
                await rel.initialize()
                last_interaction = await rel.time_since_last()
                if last_interaction and last_interaction > timedelta(hours=8):
                    return True
            except Exception:
                pass
        return False
    
    async def _check_high_stress(self) -> bool:
        """Check if user shows signs of high stress/negative emotion."""
        try:
            from serein_core.emotion import get_emotion_engine
            emotion = get_emotion_engine()
            await emotion.initialize()
            dominant, value = emotion.state.get_dominant()
            valence = emotion.state.get_valence()
            return (dominant in ["sadness", "concern", "frustration"] and value > 60) or valence < -0.5
        except Exception:
            pass
        return False
    
    async def _check_achievement(self) -> bool:
        """Check if user mentioned an achievement."""
        # This would be triggered by message analysis in the main loop
        # For now, return False - will be called explicitly
        return False
    
    async def _check_learning_gap(self) -> bool:
        """Check if user is exploring a new topic."""
        # Analyzed from conversation context
        return False
    
    async def _check_relationship_milestone(self) -> bool:
        """Check if relationship reached a milestone."""
        try:
            from serein_core.relationship import get_relationship_engine
            rel = get_relationship_engine()
            await rel.initialize()
            level = rel.state.get_level()
            return level in ["Close Companion", "Deep Bond", "Trusted Friend"]
        except Exception:
            pass
        return False
    
    async def _check_context_opportunity(self) -> bool:
        """Check for context-based suggestions."""
        # e.g., if coding, suggest break; if researching, offer to save
        return False
    
    async def _check_idle_time(self) -> bool:
        """Check if user has been idle."""
        try:
            from serein_core.relationship import get_relationship_engine
            rel = get_relationship_engine()
            await rel.initialize()
            last = await rel.time_since_last()
            return last and last > timedelta(minutes=30)
        except Exception:
            pass
        return False
    
    async def _check_goal_progress(self) -> bool:
        """Check if there are active goals to check on."""
        try:
            db = await get_database()
            # Would check goals table
            return False
        except Exception:
            pass
        return False
    
    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self._evaluate_rules()
            except Exception as e:
                logger.error(f"Proactive monitor error: {e}")
            
            await asyncio.sleep(self._check_interval)
    
    async def _evaluate_rules(self) -> None:
        """Evaluate all rules and fire eligible ones."""
        # Update context snapshot
        await self._update_context()
        
        for rule in self.rules:
            if not rule.can_fire():
                continue
            
            try:
                should_fire = await rule.condition()
                if should_fire:
                    await self._fire_rule(rule)
            except Exception as e:
                logger.error(f"Rule {rule.id} condition error: {e}")
    
    async def _update_context(self) -> None:
        """Update context snapshot from various engines."""
        self._context_snapshot = {
            "timestamp": datetime.now(),
            "time_of_day": datetime.now().hour,
            "day_of_week": datetime.now().weekday(),
        }
        
        try:
            from serein_core.context import get_context_engine
            context = get_context_engine()
            ctx = await context.get_context()
            self._context_snapshot.update({
                "session_duration": getattr(ctx, 'session_duration_minutes', 0),
                "user_activity": getattr(ctx, 'user_activity', 'unknown'),
                "active_apps": getattr(ctx, 'active_applications', []),
            })
        except Exception:
            pass
    
    async def _fire_rule(self, rule: ProactiveRule) -> None:
        """Fire a proactive rule."""
        rule.last_fired = datetime.now()
        rule.fire_count += 1
        
        # Generate message from template
        message = self._generate_message(rule)
        
        # Record event
        event = ProactiveEvent(
            id=str(uuid4()),
            rule_id=rule.id,
            timestamp=datetime.now(),
            action_type=rule.action_type,
            message=message,
        )
        self.history.append(event)
        
        # Trim history
        if len(self.history) > 500:
            self.history = self.history[-500:]
        
        # Emit event for UI/avatar to handle
        await event_bus.publish(type("Event", (), {
            "type": "proactive.triggered",
            "data": {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "action_type": rule.action_type.value,
                "message": message,
                "priority": rule.priority,
            }
        })())
        
        logger.info(f"Proactive: {rule.name} - {message[:50]}...")
    
    def _generate_message(self, rule: ProactiveRule) -> str:
        """Generate message from template using context."""
        template = rule.message_template
        ctx = self._context_snapshot
        
        # Simple template substitution
        replacements = {
            "topic": ctx.get("current_topic", "that"),
            "context": ctx.get("user_activity", "working"),
            "suggestion": "take a break" if ctx.get("session_duration", 0) > 90 else "stretch",
            "goal": "your project",
            "detail": "your interests",
        }
        
        for key, value in replacements.items():
            template = template.replace(f"{{{key}}}", str(value))
        
        return template
    
    # Public API
    
    async def trigger_proactive(self, trigger_type: ProactiveTrigger, context: Dict = None) -> List[str]:
        """Manually trigger proactive evaluation for a specific trigger."""
        if context:
            self._context_snapshot.update(context)
        
        messages = []
        for rule in self.rules:
            if rule.trigger == trigger_type and rule.can_fire():
                try:
                    should_fire = await rule.condition()
                    if should_fire:
                        await self._fire_rule(rule)
                        messages.append(self._generate_message(rule))
                except Exception as e:
                    logger.error(f"Manual trigger error for {rule.id}: {e}")
        
        return messages
    
    def add_custom_rule(self, rule: ProactiveRule) -> None:
        """Add a custom proactive rule."""
        self.rules.append(rule)
        logger.info(f"Added custom proactive rule: {rule.name}")
    
    def disable_rule(self, rule_id: str) -> bool:
        """Disable a proactive rule."""
        for rule in self.rules:
            if rule.id == rule_id:
                rule.enabled = False
                return True
        return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get proactive engine status."""
        return {
            "running": self._running,
            "total_rules": len(self.rules),
            "enabled_rules": sum(1 for r in self.rules if r.enabled),
            "recent_actions": len([e for e in self.history 
                                  if e.timestamp > datetime.now() - timedelta(hours=24)]),
            "rules": [
                {
                    "id": r.id,
                    "name": r.name,
                    "trigger": r.trigger.value,
                    "enabled": r.enabled,
                    "fire_count": r.fire_count,
                    "last_fired": r.last_fired.isoformat() if r.last_fired else None,
                }
                for r in self.rules
            ],
        }
    
    async def shutdown(self) -> None:
        """Shutdown proactive engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Proactive Intelligence Engine shutdown")


# Global instance
proactive_engine = ProactiveEngine()