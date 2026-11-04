"""
Decision Engine - The reasoning coordinator.
Chooses actions, responses, when to speak, when to remain silent.
"""
from src.core.base import BaseEngine, EngineState, event_bus
from src.database.database import Database
from datetime import datetime
from typing import Dict, List, Optional, Any, Literal
from enum import Enum
import json


class ActionType(Enum):
    RESPOND = "respond"
    ACT = "act"
    WAIT = "wait"
    CLARIFY = "clarify"
    PROACTIVE = "proactive"
    SILENCE = "silence"


class DecisionEngine(BaseEngine):
    """The reasoning coordinator - decides what Serein should do."""
    
    @property
    def engine_name(self) -> str:
        return "decision"
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._state = EngineState()
        self._pending_decision: Optional[Dict] = None
        self._decision_history: List[Dict] = []
    
    def initialize(self) -> None:
        """Initialize decision engine."""
        if not self.db:
            self.db = Database()
        
        self._state = EngineState()
        self._initialized = True
        event_bus.emit("decision.initialized", {})
    
    def save_state(self) -> None:
        pass
    
    def get_state(self) -> EngineState:
        return self._state
    
    def decide(
        self, 
        user_input: str,
        context: Dict,
        emotions: Dict,
        relationship: Dict,
        growth: Dict,
        memories: List,
        safety_check: Dict
    ) -> Dict[str, Any]:
        """
        Main decision-making function.
        Returns decision with action type and parameters.
        """
        # Safety first - if safety says no, we don't act
        if not safety_check.get("allowed", True):
            return {
                "action": ActionType.RESPOND.value,
                "response_type": "safety_decline",
                "reason": safety_check.get("reason", "Action not permitted"),
                "content": None,
                "confidence": 1.0
            }
        
        # Analyze input
        analysis = self._analyze_input(user_input, context)
        
        # Apply contextual modifiers
        decision = self._make_decision(
            analysis, context, emotions, relationship, growth, memories
        )
        
        # Record decision
        self._record_decision(decision, analysis)
        
        return decision
    
    def _analyze_input(self, user_input: str, context: Dict) -> Dict[str, Any]:
        """Analyzes user input to understand intent and urgency."""
        text = user_input.lower().strip()
        
        analysis = {
            "is_question": "?" in text,
            "is_command": any(text.startswith(cmd) for cmd in 
                            ["please", "can you", "could you", "would you", "do ", "make ", "create ", "run ", "open ", "close ", "stop ", "start "]),
            "is_greeting": any(g in text for g in ["hello", "hi ", "hey", "good morning", "good evening", "good afternoon"]),
            "is_farewell": any(g in text for g in ["bye", "goodbye", "see you", "later", "exit", "quit"]),
            "is_emotional": any(e in text for e in ["feel", "feeling", "sad", "happy", "angry", "frustrated", "excited", "worried", "stressed", "tired"]),
            "is_technical": any(t in text for t in ["code", "error", "bug", "debug", "function", "api", "database", "server", "deploy", "git"]),
            "is_personal": any(p in text for p in ["my ", "i am", "i'm ", "i ", "me ", "mine"]),
            "urgency": "low",
            "complexity": "simple",
            "requires_action": False,
            "topic": "general"
        }
        
        # Detect urgency
        urgent_words = ["urgent", "asap", "immediately", "right now", "emergency", "critical", "broken", "down"]
        if any(w in text for w in urgent_words):
            analysis["urgency"] = "high"
        elif any(w in text for w in ["soon", "when you can", "later today"]):
            analysis["urgency"] = "medium"
        
        # Detect complexity
        if len(text.split()) > 30 or analysis["is_technical"]:
            analysis["complexity"] = "complex"
        elif len(text.split()) > 15:
            analysis["complexity"] = "moderate"
        
        # Detect if action required
        action_verbs = ["open", "close", "run", "start", "stop", "create", "delete", "move", "copy", "install", "update", "send", "write", "read"]
        analysis["requires_action"] = any(v in text for v in action_verbs) and not analysis["is_question"]
        
        # Detect topic
        if analysis["is_technical"]:
            analysis["topic"] = "technical"
        elif analysis["is_emotional"]:
            analysis["topic"] = "emotional"
        elif analysis["is_personal"]:
            analysis["topic"] = "personal"
        elif "goal" in text or "plan" in text:
            analysis["topic"] = "planning"
        elif "remember" in text or "memory" in text:
            analysis["topic"] = "memory"
        
        return analysis
    
    def _make_decision(
        self,
        analysis: Dict,
        context: Dict,
        emotions: Dict,
        relationship: Dict,
        growth: Dict,
        memories: List
    ) -> Dict[str, Any]:
        """Core decision logic."""
        
        # Farewell - always respond warmly
        if analysis["is_farewell"]:
            return {
                "action": ActionType.RESPOND.value,
                "response_type": "farewell",
                "content": None,  # Let persona generate
                "confidence": 1.0
            }
        
        # Greeting - respond and maybe be proactive
        if analysis["is_greeting"]:
            proactive = relationship.get("level") in ["Close Companion", "Deep Bond"] and \
                       context.get("session_duration", 0) == 0
            return {
                "action": ActionType.PROACTIVE.value if proactive else ActionType.RESPOND.value,
                "response_type": "greeting",
                "proactive_suggestions": context.get("suggestions", []) if proactive else [],
                "content": None,
                "confidence": 0.9
            }
        
        # High urgency - prioritize action/response
        if analysis["urgency"] == "high":
            if analysis["requires_action"]:
                return {
                    "action": ActionType.ACT.value,
                    "response_type": "action_confirmation",
                    "action_data": {"intent": analysis["topic"], "input": analysis},
                    "confidence": 0.9
                }
            else:
                return {
                    "action": ActionType.RESPOND.value,
                    "response_type": "urgent_response",
                    "content": None,
                    "confidence": 0.9
                }
        
        # Complex technical questions - might need clarification
        if analysis["complexity"] == "complex" and analysis["is_question"] and analysis["is_technical"]:
            clarity = self._assess_clarity(analysis, memories)
            if clarity < 0.6:
                return {
                    "action": ActionType.CLARIFY.value,
                    "response_type": "clarification_needed",
                    "clarification_areas": self._identify_clarification_needs(analysis),
                    "confidence": 0.7
                }
        
        # Emotional input - respond with empathy
        if analysis["is_emotional"]:
            return {
                "action": ActionType.RESPOND.value,
                "response_type": "empathetic",
                "emotional_tone": emotions.get("dominant", "care"),
                "content": None,
                "confidence": 0.85
            }
        
        # Commands/actions
        if analysis["requires_action"] and not analysis["is_question"]:
            return {
                "action": ActionType.ACT.value,
                "response_type": "action_execution",
                "action_data": {"intent": analysis["topic"], "input": analysis},
                "confidence": 0.8
            }
        
        # Questions - respond
        if analysis["is_question"]:
            # Check if we have relevant memories
            has_context = len(memories) > 0
            return {
                "action": ActionType.RESPOND.value,
                "response_type": "informative" if has_context else "general",
                "use_memories": has_context,
                "content": None,
                "confidence": 0.8
            }
        
        # Personal sharing - acknowledge and respond
        if analysis["is_personal"]:
            return {
                "action": ActionType.RESPOND.value,
                "response_type": "personal_acknowledgment",
                "content": None,
                "confidence": 0.75
            }
        
        # Default - conversational response
        # But check if we should be proactive
        if relationship.get("level") in ["Close Companion", "Deep Bond"]:
            suggestions = context.get("suggestions", [])
            if suggestions and context.get("session_duration", 0) > 10:
                return {
                    "action": ActionType.PROACTIVE.value,
                    "response_type": "proactive_suggestion",
                    "suggestions": suggestions[:2],
                    "confidence": 0.6
                }
        
        return {
            "action": ActionType.RESPOND.value,
            "response_type": "conversational",
            "content": None,
            "confidence": 0.7
        }
    
    def _assess_clarity(self, analysis: Dict, memories: List) -> float:
        """Assesses how clear the user's request is (0-1)."""
        # Simple heuristic - could be enhanced
        clarity = 0.5
        
        # More specific words = more clarity
        specific_indicators = ["specific", "exact", "particular", "detailed", "step by step"]
        if any(w in analysis.get("input", "").lower() for w in specific_indicators):
            clarity += 0.2
        
        # Context from memories helps
        if memories:
            clarity += 0.2
        
        # Technical questions with context
        if analysis["is_technical"] and memories:
            clarity += 0.1
        
        return min(1.0, clarity)
    
    def _identify_clarification_needs(self, analysis: Dict) -> List[str]:
        """Identifies what clarification is needed."""
        needs = []
        text = analysis.get("input", "").lower()
        
        if "how" in text and not any(w in text for w in ["code", "function", "api", "specific"]):
            needs.append("What specific outcome are you looking for?")
        
        if "what" in text and "options" not in text:
            needs.append("Do you want options or a specific recommendation?")
        
        if analysis["is_technical"] and "language" not in text and "framework" not in text:
            needs.append("What language or framework are you using?")
        
        return needs[:3]  # Max 3 clarification points
    
    def _record_decision(self, decision: Dict, analysis: Dict) -> None:
        """Records decision for learning."""
        self._decision_history.append({
            "timestamp": datetime.now().isoformat(),
            "decision": decision,
            "analysis": analysis
        })
        self._decision_history = self._decision_history[-200:]
    
    def should_speak_now(self, context: Dict, emotions: Dict, relationship: Dict) -> bool:
        """Decides if Serein should proactively speak."""
        # Don't interrupt if user is typing/active
        if context.get("user_activity") in ["typing", "coding", "in_meeting"]:
            return False
        
        # Don't speak too often
        last_decision = self._decision_history[-1] if self._decision_history else None
        if last_decision:
            last_time = datetime.fromisoformat(last_decision["timestamp"])
            if (datetime.now() - last_time).seconds < 30:
                return False
        
        # Proactive based on relationship
        if relationship.get("level") in ["Close Companion", "Deep Bond"]:
            return True
        elif relationship.get("level") == "Trusted Friend":
            return context.get("session_duration", 0) > 30
        
        return False
    
    def get_decision_context(self) -> str:
        """Returns decision-making context for prompts."""
        recent = self._decision_history[-5:] if self._decision_history else []
        
        lines = ["DECISION CONTEXT:"]
        lines.append(f"- Recent decisions: {len(recent)}")
        
        if recent:
            actions = [d["decision"]["action"] for d in recent]
            lines.append(f"- Recent actions: {', '.join(actions)}")
        
        return "\n".join(lines)
    
    def close(self) -> None:
        super().close()