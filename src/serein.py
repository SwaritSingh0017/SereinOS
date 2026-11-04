"""
SereinOS Core - The main orchestration layer that ties all engines together.
This is the central nervous system of SereinOS.
"""
from src.core.base import event_bus
from src.database.database import Database
from src.core.identity.identity_engine import IdentityEngine
from src.core.persona.persona_engine import PersonaEngine
from src.core.emotion.emotion_engine import EmotionEngine
from src.core.memory.memory_engine import MemoryEngine
from src.core.relationship.relationship_engine import RelationshipEngine
from src.core.growth.growth_engine import GrowthEngine
from src.core.context.context_engine import ContextEngine
from src.core.decision.decision_engine import DecisionEngine
from src.core.safety.safety_layer import SafetyLayer
from src.core.agent.agent_engine import AgentEngine
from src.voice.voice_engine import VoiceEngine, VoiceConfig
from src.vision.vision_engine import VisionEngine, VisionConfig
from src.ui.ui_engine import UIEngine, UIConfig, UIMode
from src.ai.providers import ProviderManager
from src.config import config_manager
from datetime import datetime
from typing import Dict, List, Optional, Any
import time
import json
import threading


class SereinOS:
    """Main orchestration class for SereinOS."""
    
    def __init__(self, config: Dict = None):
        self.config = config_manager.get()
        self.db: Optional[Database] = None
        
        # Core engines
        self.identity: Optional[IdentityEngine] = None
        self.persona: Optional[PersonaEngine] = None
        self.emotion: Optional[EmotionEngine] = None
        self.memory: Optional[MemoryEngine] = None
        self.relationship: Optional[RelationshipEngine] = None
        self.growth: Optional[GrowthEngine] = None
        self.context: Optional[ContextEngine] = None
        self.decision: Optional[DecisionEngine] = None
        self.safety: Optional[SafetyLayer] = None
        self.agent: Optional[AgentEngine] = None
        
        # Perception engines
        self.voice: Optional[VoiceEngine] = None
        self._initialized = False
        self._running = False
        self._session_start = datetime.now()
        self._interaction_count = 0
        self._last_interaction = None
        self._ui_thread: Optional[threading.Thread] = None
    
    def initialize(self) -> None:
        """Initialize all engines and systems."""
        print("\n[SereinOS] Initializing...")
        
        # Database
        self.db = Database()
        self.db.initialize_serein()
        print("[SereinOS] Database ready")
        
        # Initialize engines in dependency order
        self.identity = IdentityEngine(self.db)
        self.identity.initialize()
        print("[SereinOS] Identity Engine ready")
        
        self.persona = PersonaEngine(self.db)
        self.persona.initialize()
        print("[SereinOS] Persona Engine ready")
        
        self.emotion = EmotionEngine(self.db)
        self.emotion.initialize()
        print("[SereinOS] Emotion Engine ready")
        
        self.memory = MemoryEngine(self.db)
        self.memory.initialize()
        print("[SereinOS] Memory Engine ready")
        
        self.relationship = RelationshipEngine(self.db)
        self.relationship.initialize()
        print("[SereinOS] Relationship Engine ready")
        
        self.growth = GrowthEngine(self.db)
        self.growth.initialize()
        print("[SereinOS] Growth Engine ready")
        
        self.context = ContextEngine(self.db)
        self.context.initialize()
        print("[SereinOS] Context Engine ready")
        
        self.decision = DecisionEngine(self.db)
        self.decision.initialize()
        print("[SereinOS] Decision Engine ready")
        
        self.safety = SafetyLayer(self.db)
        self.safety.initialize()
        print("[SereinOS] Safety Layer ready")
        
        self.agent = AgentEngine(self.db, self.safety)
        self.agent.initialize()
        print("[SereinOS] Agent Engine ready")
        
        # Perception engines (Voice & Vision)
        self.voice = VoiceEngine(VoiceConfig(
            enabled=self.config.voice.enabled if hasattr(self.config, 'voice') else False,
            stt_backend="auto",
            tts_backend="auto"
        ))
        print(f"[SereinOS] Voice Engine ready ({self.voice.get_status()['stt']}/{self.voice.get_status()['tts']})")
        
        self.vision = VisionEngine(VisionConfig(
            enabled=self.config.vision.enabled if hasattr(self.config, 'vision') else False,
            ocr_backend="auto",
            detection_backend="auto"
        ))
        print(f"[SereinOS] Vision Engine ready ({self.vision.get_status()['ocr_backend']}/{self.vision.get_status()['detector_backend']})")
        
        # UI Engine
        self.ui = UIEngine(UIConfig(
            mode=UIMode.CONVERSATIONAL,
            theme="dark"
        ))
        if self.ui.initialize():
            print("[SereinOS] UI Engine ready")
        else:
            print("[SereinOS] UI Engine failed to initialize")
        
        # AI Provider
        self.provider_manager = ProviderManager(self.config)
        
        # Context builder with all engines
        self.context_builder = SereinContextBuilder(self)
        
        self._initialized = True
        self._running = True
        
        # Subscribe to events
        self._subscribe_events()
        
        print("[SereinOS] All systems initialized\n")
        
        event_bus.emit("serein.initialized", {
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0"
        })
        self.provider_manager = ProviderManager(self.config)
        
        # Context builder with all engines
        self.context_builder = SereinContextBuilder(self)
        
        self._initialized = True
        self._running = True
        
        # Subscribe to events
        self._subscribe_events()
        
        print("[SereinOS] All systems initialized\n")
        
        event_bus.emit("serein.initialized", {
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0"
        })
    
    def _subscribe_events(self) -> None:
        """Subscribe to inter-engine events."""
        event_bus.subscribe("memory.stored", self._on_memory_stored)
        event_bus.subscribe("emotion.changed", self._on_emotion_changed)
        event_bus.subscribe("relationship.updated", self._on_relationship_updated)
        event_bus.subscribe("growth.updated", self._on_growth_updated)
        event_bus.subscribe("context.activity_changed", self._on_activity_changed)
        event_bus.subscribe("safety.approval_requested", self._on_approval_requested)
    
    def _on_memory_stored(self, data: Dict) -> None:
        """Handle new memory stored."""
        # Could trigger consolidation, etc.
        pass
    
    def _on_emotion_changed(self, data: Dict) -> None:
        """Handle emotion changes."""
        pass
    
    def _on_relationship_updated(self, data: Dict) -> None:
        """Handle relationship updates."""
        pass
    
    def _on_growth_updated(self, data: Dict) -> None:
        """Handle growth updates."""
        pass
    
    def _on_activity_changed(self, data: Dict) -> None:
        """Handle user activity changes."""
        pass
    
    def _on_approval_requested(self, data: Dict) -> None:
        """Handle safety approval requests."""
        # In a real implementation, this would prompt the user
        print(f"\n[SAFETY] Approval requested for: {data['action']}")
        print(f"Reason: {data['reason']}")
        # Auto-approve for now
        self.safety.approve_action(data['id'], True, "Auto-approved")
    
    def process_input(self, user_input: str) -> Dict[str, Any]:
        """
        Main input processing pipeline.
        Returns the decision and response.
        """
        if not self._initialized:
            raise RuntimeError("SereinOS not initialized")
        
        start_time = time.time()
        
        # Update context
        self.context._update_context()
        
        # Record interaction
        self._interaction_count += 1
        self._last_interaction = datetime.now()
        self.identity.update_last_seen()
        
        # Process through engines
        
        # 1. Memory: Evaluate and store
        memory_record = self.memory.evaluate_and_store(
            user_input, 
            context=self._get_current_context()
        )
        
        # 2. Emotion: Process message
        emotion_deltas = self.emotion.process_message_emotion(
            user_input, 
            context=self._get_current_context()
        )
        
        # 3. Growth: Process message
        growth_deltas = self.growth.process_interaction(user_input)
        
        # 4. Relationship: Record interaction
        rel_deltas = self.relationship.record_interaction(
            duration_minutes=1,
            quality=self._assess_interaction_quality(user_input),
            context=self._get_current_context()
        )
        
        # 5. Get relevant memories
        relevant_memories = self.memory.recall_relevant(
            user_input,
            context=self._get_current_context(),
            limit=10
        )
        
        # 6. Decision: Decide what to do
        decision = self.decision.decide(
            user_input=user_input,
            context=self._get_current_context(),
            emotions=self.emotion.get_emotions().to_dict(),
            relationship=self.relationship.get_relationship().to_dict(),
            growth=self.growth.get_growth().to_dict(),
            memories=[m.to_dict() for m in relevant_memories],
            safety_check=self.safety.validate_action("conversation", {"input": user_input})
        )
        
        # 7. Execute decision
        response = self._execute_decision(decision, user_input, relevant_memories)
        
        # 8. Post-process response through persona
        final_response = self.persona.format_response(
            response.get("content", ""),
            emotion_context=self.emotion.get_emotional_influence_on_response()
        )
        
        # Update response with formatted content
        response["content"] = final_response
        response["processing_time"] = time.time() - start_time
        response["engines_used"] = {
            "memory": memory_record is not None,
            "emotion": len(emotion_deltas) > 0,
            "growth": len(growth_deltas) > 0,
            "relationship": True,
            "decision": True,
            "safety": True
        }
        
        return response
    
    def _get_current_context(self) -> Dict:
        """Gathers current context from all engines."""
        return {
            "time": self.context.get_time_context(),
            "system": self.context.get_system_context(),
            "session_duration": self.context.get_context().session_duration_minutes,
            "user_activity": self.context.get_context().user_activity,
            "suggestions": self.context.get_contextual_suggestions(),
            "relationship_level": self.relationship.get_relationship().get_relationship_level()
        }
    
    def _assess_interaction_quality(self, user_input: str) -> str:
        """Assesses interaction quality for relationship tracking."""
        text = user_input.lower()
        
        positive = ["thank", "thanks", "great", "awesome", "love", "helpful", "perfect", "yes"]
        negative = ["no", "wrong", "bad", "terrible", "hate", "stupid", "useless", "annoying"]
        
        pos_count = sum(1 for w in positive if w in text)
        neg_count = sum(1 for w in negative if w in text)
        
        if pos_count > neg_count:
            return "positive"
        elif neg_count > pos_count:
            return "negative"
        return "neutral"
    
    def _execute_decision(self, decision: Dict, user_input: str, memories: List) -> Dict:
        """Executes the decision and generates response."""
        action = decision.get("action", "respond")
        
        if action == "respond" or action == "proactive":
            return self._generate_response(decision, user_input, memories)
        
        elif action == "act":
            return self._execute_action(decision, user_input)
        
        elif action == "clarify":
            return {
                "action": "clarify",
                "content": self._generate_clarification(decision),
                "clarification_needed": decision.get("clarification_areas", [])
            }
        
        elif action == "wait":
            return {
                "action": "wait",
                "content": "...",
                "reason": "Waiting for more input"
            }
        
        else:
            return {
                "action": "respond",
                "content": "I'm not sure how to respond to that.",
                "fallback": True
            }
    
    def _generate_response(self, decision: Dict, user_input: str, memories: List) -> Dict:
        """Generates LLM response with full context."""
        # Build comprehensive context
        prompt = self.context_builder.build_context(
            user_input=user_input,
            decision=decision,
            memories=memories
        )
        
        # Get response from AI (with fallback)
        response_text = self.provider_manager.generate_response(prompt)
        
        return {
            "action": decision["action"],
            "response_type": decision.get("response_type", "conversational"),
            "content": response_text,
            "confidence": decision.get("confidence", 0.7),
            "decision": decision,
            "provider": self.provider_manager.get_current_provider_name()
        }
    
    def _execute_action(self, decision: Dict, user_input: str) -> Dict:
        """Executes an action via agent engine."""
        action_data = decision.get("action_data", {})
        action_type = action_data.get("intent", "unknown")
        
        # Map intent to agent action
        action_mapping = {
            "file_read": "file_read",
            "file_write": "file_write",
            "file_delete": "file_delete",
            "file_list": "file_list",
            "file_move": "file_move",
            "app_launch": "app_launch",
            "app_close": "app_close",
            "app_list": "app_list",
            "system_command": "system_command",
            "system_info": "system_info",
            "network_request": "network_request",
            "task_create": "task_create",
            "task_complete": "task_complete",
            "task_list": "task_list",
            "reminder_set": "reminder_set",
            "note_save": "note_save"
        }
        
        agent_action = action_mapping.get(action_type, "note_save")
        params = action_data.get("params", {})
        
        result = self.agent.execute_action(agent_action, params)
        
        # Generate response about the action
        if result.get("status") == "completed":
            content = f"Done. {self._format_action_result(result)}"
        elif result.get("approval_required"):
            content = result.get("message", "This action requires your approval.")
        else:
            content = f"I couldn't complete that: {result.get('error', 'Unknown error')}"
        
        return {
            "action": "act",
            "response_type": "action_result",
            "content": content,
            "action_result": result
        }
    
    def _format_action_result(self, result: Dict) -> str:
        """Formats action result for response."""
        if "content" in result:
            return f"Here's what I found:\n{result['content'][:500]}"
        elif "files" in result:
            return f"Found {len(result['files'])} items."
        elif "apps" in result:
            return f"Found {len(result['apps'])} running applications."
        elif "task_id" in result:
            return f"Task created (ID: {result['task_id']})."
        else:
            return "Action completed."
    
    def _generate_clarification(self, decision: Dict) -> str:
        """Generates clarification response."""
        areas = decision.get("clarification_areas", [])
        if areas:
            return f"I'd like to clarify a few things:\n" + "\n".join(f"• {a}" for a in areas)
        return "Could you clarify what you'd like me to do?"
    
    def get_status(self) -> Dict:
        """Returns overall system status."""
        return {
            "initialized": self._initialized,
            "running": self._running,
            "session_duration_minutes": int((datetime.now() - self._session_start).total_seconds() / 60),
            "interaction_count": self._interaction_count,
            "identity": self.identity.get_serein_identity().to_dict() if self.identity else {},
            "user": self.identity.get_user_identity().to_dict() if self.identity else {},
            "emotions": self.emotion.get_emotions().to_dict() if self.emotion else {},
            "relationship": self.relationship.get_relationship().to_dict() if self.relationship else {},
            "growth": self.growth.get_growth().to_dict() if self.growth else {},
            "memory_stats": self.memory.get_stats() if self.memory else {},
            "context": self.context.get_context().to_dict() if self.context else {},
            "permissions": self.safety.get_permissions() if self.safety else {}
        }
    
    def shutdown(self) -> None:
        """Graceful shutdown."""
        print("\n[SereinOS] Shutting down...")
        
        self._running = False
        
        # Consolidate memories
        if self.memory:
            self.memory.consolidate_memories()
        
        # Save all engine states
        for engine in [self.identity, self.persona, self.emotion, self.memory,
                       self.relationship, self.growth, self.safety]:
            if engine:
                engine.close()
        
        if self.db:
            self.db.close()
        
        print("[SereinOS] Shutdown complete\n")
    
    def get_greeting(self) -> str:
        """Returns a contextual greeting."""
        time_ctx = self.context.get_time_context()
        rel_level = self.relationship.get_relationship().get_relationship_level()
        user_name = self.identity.get_user_display_name()
        
        time_greeting = {
            "morning": "Good morning",
            "afternoon": "Good afternoon", 
            "evening": "Good evening",
            "night": "Still up?"
        }.get(time_ctx.get("time_of_day", "day"), "Hello")
        
        if rel_level == "New Acquaintance":
            return f"{time_greeting}, {user_name}. I'm Serein. How can I help you today?"
        elif rel_level in ["Familiar Presence", "Trusted Friend"]:
            return f"{time_greeting}, {user_name}. Good to see you again."
        else:
            return f"{time_greeting}, {user_name}. Welcome back."
    
    def get_introduction(self) -> str:
        """Returns Serein's introduction."""
        return self.identity.get_serein_introduction()


class SereinContextBuilder:
    """Builds comprehensive context prompts for the LLM."""
    
    def __init__(self, serein_os: SereinOS):
        self.serein = serein_os
        self.core_prompt = self._load_core_prompt()
    
    def _load_core_prompt(self) -> str:
        """Loads the core prompt from file."""
        try:
            with open("docs/SEREIN_CORE_PROMPT.md", "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "You are Serein, an AI companion."
    
    def build_context(self, user_input: str, decision: Dict, memories: List) -> str:
        """Builds the complete context prompt for the LLM."""
        sections = []
        
        # Core prompt
        sections.append(self.core_prompt)
        sections.append("\n" + "="*50 + "\n")
        
        # Identity
        sections.append(self.serein.identity.get_identity_context())
        sections.append("\n" + "-"*30 + "\n")
        
        # Personality / Persona
        sections.append(self.serein.persona.get_speaking_style_prompt())
        sections.append("\n" + "-"*30 + "\n")
        
        # Emotions
        sections.append(self.serein.emotion.get_emotional_context())
        sections.append("\n" + "-"*30 + "\n")
        
        # Relationship
        sections.append(self.serein.relationship.get_relationship_context())
        sections.append("\n" + "-"*30 + "\n")
        
        # Growth/Interests
        sections.append(self.serein.growth.get_growth_context())
        sections.append("\n" + "-"*30 + "\n")
        
        # Context
        sections.append(self.serein.context.get_context_context())
        sections.append("\n" + "-"*30 + "\n")
        
        # Memories
        memory_context = self.serein.memory.get_memory_context(user_input, limit=8)
        sections.append(memory_context)
        sections.append("\n" + "="*50 + "\n")
        
        # Decision context
        sections.append(self.serein.decision.get_decision_context())
        sections.append("\n" + "="*50 + "\n")
        
        # Agent capabilities
        sections.append(self.serein.agent.get_agent_context())
        sections.append("\n" + "="*50 + "\n")
        
        # Current message
        sections.append(f"CURRENT MESSAGE:\n{user_input}")
        sections.append("\n" + "="*50 + "\n")
        
        # Decision guidance
        response_type = decision.get("response_type", "conversational")
        sections.append(f"RESPONSE GUIDANCE: {response_type}")
        
        if decision.get("use_memories"):
            sections.append("- Reference relevant memories naturally in your response")
        
        emotional_influence = self.serein.emotion.get_emotional_influence_on_response()
        if emotional_influence.get("tone_modifier"):
            sections.append(f"- Tone: {emotional_influence['tone_modifier']}")
        
        relationship_influence = self.serein.relationship.get_relationship_influence()
        if relationship_influence.get("proactivity"):
            sections.append("- You may be proactive and suggest things")
        
        persona_additions = self.serein.persona.get_persona_prompt_additions(
            self.serein.relationship.get_relationship().get_relationship_level()
        )
        if persona_additions:
            sections.append(persona_additions)
        
        sections.append("\nRespond as Serein:")
        
        return "\n".join(sections)