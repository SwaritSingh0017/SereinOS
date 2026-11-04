"""
Compatibility layer: Old src/serein.py interface backed by new serein-core.
This allows main.py to continue working while we migrate to serein-core.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'serein-core', 'src'))

from serein_core import (
    SereinCore,
    load_config,
    ContextEngine,
    DecisionEngine,
    AgentEngine,
    SafetyLayer,
    VisionEngine,
    VoiceEngine,
)
from serein_core.config import CoreConfig
from serein_core.events import event_bus
from serein_core.identity import IdentityEngine
from serein_core.persona import PersonaEngine
from serein_core.emotion import EmotionEngine
from serein_core.memory import MemoryEngine
from serein_core.relationship import RelationshipEngine
from serein_core.growth import GrowthEngine
from serein_core.ai import BrainRouter
from serein_core.command_bus import CommandBus
from serein_core.scheduler import Scheduler
from serein_core.database import Database
from serein_core.base import ContextState

# Re-export everything the old src.serein expected
class SereinOSCompat:
    """
    Compatibility wrapper that mimics old SereinOS interface
    but uses serein-core underneath.
    """
    
    def __init__(self, config=None):
        self.config = config or CoreConfig()
        self.db = None
        self._running = False
        
        # Core engines (using serein-core implementations)
        self.identity = IdentityEngine()
        self.persona = PersonaEngine()
        self.emotion = EmotionEngine()
        self.memory = MemoryEngine()
        self.relationship = RelationshipEngine()
        self.growth = GrowthEngine()
        self.context = ContextEngine()
        
        # Decision & Safety
        self.decision = DecisionEngine()
        self.safety = SafetyLayer()
        self.agent = AgentEngine()
        
        # Perception
        self.voice = VoiceEngine()
        self.vision = VisionEngine()
        
        # Services
        self.router = None
        self.command_bus = None
        self.scheduler = None
        self.ui = None
        
        self._session_start = __import__('datetime').datetime.now()
        self._interaction_count = 0
        self._last_interaction = None
    
    def initialize(self):
        """Initialize all subsystems."""
        import asyncio
        asyncio.run(self._initialize_async())
    
    async def _initialize_async(self):
        import logging
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger("serein.compat")
        
        logger.info("[SereinOS] Initializing...")
        
        # Database
        self.db = Database(
            path=self.config.database.path,
            encryption_key=self.config.database.encryption_key,
            pragmas=self.config.database.pragmas,
        )
        await self.db.connect()
        logger.info("[OK] Database connected")
        
        # Engines
        await self.identity.initialize()
        logger.info("[OK] Identity Engine")
        
        await self.persona.initialize()
        logger.info("[OK] Persona Engine")
        
        await self.emotion.initialize()
        logger.info("[OK] Emotion Engine")
        
        await self.memory.initialize()
        logger.info("[OK] Memory Engine")
        
        await self.relationship.initialize()
        logger.info("[OK] Relationship Engine")
        
        await self.growth.initialize()
        logger.info("[OK] Growth Engine")
        
        await self.context.initialize()
        logger.info("[OK] Context Engine")
        
        await self.decision.initialize()
        logger.info("[OK] Decision Engine")
        
        await self.safety.initialize()
        logger.info("[OK] Safety Layer")
        
        await self.agent.initialize()
        logger.info("[OK] Agent Engine")
        
        # AI Router
        self.router = BrainRouter(self.config)
        logger.info("[OK] AI Brain Router")
        
        # Command Bus
        self.command_bus = CommandBus()
        await self.command_bus.start()
        logger.info("[OK] Command Bus")
        
        # Scheduler
        self.scheduler = Scheduler()
        await self.scheduler.start()
        logger.info("[OK] Scheduler")
        
        # Voice & Vision
        await self.voice.initialize()
        logger.info("[OK] Voice Engine")
        
        await self.vision.initialize()
        logger.info("[OK] Vision Engine")
        
        logger.info("[SereinOS] All systems initialized")
        self._running = True
    
    def process_input(self, user_input):
        """Process user input through the full pipeline."""
        import asyncio
        return asyncio.run(self._process_input_async(user_input))
    
    async def _process_input_async(self, user_input):
        if not self._running:
            raise RuntimeError("Service not running")
        
        # This is a simplified version - full pipeline would use decision engine
        from serein_core.ai import ModelTier
        
        context = {}
        context["time_since_last"] = await self.relationship.time_since_last()
        context["relationship_level"] = self.relationship.get_relationship_level()
        
        # Update context from context engine
        context_state = self.context.get_context()
        context["current_time"] = context_state.current_time
        context["date"] = context_state.date
        context["day_of_week"] = context_state.day_of_week
        context["active_applications"] = context_state.active_applications
        context["internet_connected"] = context_state.internet_connected
        context["session_duration_minutes"] = context_state.session_duration_minutes
        
        # Memory: evaluate and store
        await self.memory.evaluate_and_store(user_input, context)
        
        # Emotion: process message
        await self.emotion.process_message(user_input, context)
        
        # Growth: process message
        await self.growth.process(user_input, context)
        
        # Relationship: record interaction
        await self.relationship.record_interaction(quality="neutral")
        
        # Get relevant memories
        memories = await self.memory.recall(user_input, context, limit=10)
        
        # Get influences
        emotion_influence = await self.emotion.get_influence()
        relationship_influence = await self.relationship.get_influence()
        growth_influence = await self.growth.get_influence()
        
        # Build prompt
        prompt = self._build_prompt(user_input, memories, context, 
                                   emotion_influence, relationship_influence, growth_influence)
        
        # Route to AI
        tier = "fast" if len(user_input) < 100 else "smart"
        response = await self.router.chat(
            [{"role": "user", "content": prompt}],
            tier=ModelTier(tier),
        )
        
        # Format through persona
        final_response = self.persona.format_response(
            response.content,
            emotion_influence
        )
        
        return final_response
    
    def _build_prompt(self, user_input, memories, context, emotion_inf, relationship_inf, growth_inf):
        parts = []
        
        # Core identity/persona
        parts.append(self.identity.get_introduction())
        parts.append(self.persona.get_speaking_prompt())
        parts.append(self.emotion.get_context())
        parts.append(self.relationship.get_context())
        parts.append(self.growth.get_context())
        
        # Memories
        if memories:
            parts.append("RELEVANT MEMORIES:")
            for i, mem in enumerate(memories, 1):
                parts.append(f"  {i}. {mem.content[:200]}...")
        
        # Context
        parts.append(f"\nCURRENT TIME: {context.get('current_time', 'unknown')}")
        parts.append(f"DAY: {context.get('day_of_week', 'unknown')}")
        parts.append(f"SESSION: {context.get('session_duration_minutes', 0)} minutes")
        
        # Guidance
        if emotion_inf.get("tone_modifier"):
            parts.append(f"TONE: {emotion_inf['tone_modifier']}")
        if relationship_inf.get("proactivity", 0) > 0:
            parts.append("- You may be proactive and suggest relevant things")
        
        parts.append(f"\nUSER: {user_input}")
        parts.append("\nRESPOND AS SEREIN:")
        
        return "\n".join(parts)
    
    def shutdown(self):
        """Shutdown all systems."""
        import asyncio
        asyncio.run(self._shutdown_async())
    
    async def _shutdown_async(self):
        import logging
        logger = logging.getLogger("serein.compat")
        logger.info("[SereinOS] Shutting down...")
        
        if self.scheduler:
            await self.scheduler.stop()
        if self.command_bus:
            await self.command_bus.stop()
        if self.router:
            await self.router.close()
        if self.db:
            await self.db.close()
        
        self._running = False
        logger.info("[SereinOS] Shutdown complete")


# Alias for backward compatibility
SereinOS = SereinOSCompat


def create_serein(config=None):
    """Factory function."""
    return SereinOSCompat(config)