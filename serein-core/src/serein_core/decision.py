"""Deterministic Decision Engine for SereinOS.

The Decision Engine converts an already-classified intent into an execution
plan. It never executes OS actions itself. Execution is delegated to the
Command Bus, Scheduler, or Agent system by later stages.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from serein_core.base import BaseEngine, EngineState, event_bus
if TYPE_CHECKING:
    from serein_core.database import Database
from serein_core.intent.classifier import IntentResult


class ActionType(str, Enum):
    RESPOND = "respond"
    EXECUTE = "execute"
    SCHEDULE = "schedule"
    DELEGATE = "delegate"
    WAIT = "wait"
    CLARIFY = "clarify"
    PROACTIVE = "proactive"
    SILENCE = "silence"


class Route(str, Enum):
    CONVERSATION = "conversation"
    BRAIN = "brain"
    COMMAND_BUS = "command_bus"
    SCHEDULER = "scheduler"
    AGENT = "agent"
    SAFETY = "safety"
    NONE = "none"


@dataclass(frozen=True)
class Decision:
    action: ActionType
    route: Route
    reason: str
    confidence: float = 1.0
    requires_confirmation: bool = False
    content: Optional[str] = None
    intent: Optional[str] = None
    entities: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "route": self.route.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "requires_confirmation": self.requires_confirmation,
            "content": self.content,
            "intent": self.intent,
            "entities": dict(self.entities),
            "metadata": dict(self.metadata),
        }


class DecisionEngine(BaseEngine):
    """Selects the next subsystem for an intent without executing it."""

    @property
    def engine_name(self) -> str:
        return "decision"

    def __init__(self, database: "Database | None" = None):
        super().__init__(database)
        self._state = EngineState()
        self._decision_history: List[Dict[str, Any]] = []

    async def initialize(self) -> None:
        if not self.db:
            from serein_core.database import Database
            self.db = Database()
        self._state = EngineState()
        self._initialized = True
        await event_bus.publish("decision.initialized", {})

    async def save_state(self) -> None:
        self._state.updated_at = datetime.now()

    def get_state(self) -> EngineState:
        return self._state

    async def decide(
        self,
        intent: IntentResult | str,
        *,
        context: Dict[str, Any] | None = None,
        safety: Dict[str, Any] | None = None,
        user_confirmation: bool = False,
    ) -> Decision:
        """Create a routing decision from an IntentResult.

        ``intent`` may temporarily be a string for compatibility, but the
        canonical interface is IntentResult. No command, scheduler, or agent
        is invoked here.
        """
        context = context or {}
        safety = safety or {"allowed": True}
        result = self._coerce_intent(intent)

        if not safety.get("allowed", True):
            return self._record(Decision(
                action=ActionType.RESPOND,
                route=Route.SAFETY,
                reason=safety.get("reason", "Action is not permitted."),
                confidence=1.0,
                intent=result.intent,
                entities=result.entities,
                metadata={"safety_blocked": True},
            ))

        if result.intent == "unknown" or result.confidence < 0.35:
            return self._record(self._clarify(result, "Intent confidence is too low."))

        if result.requires_confirmation and not user_confirmation:
            return self._record(Decision(
                action=ActionType.CLARIFY,
                route=Route.SAFETY,
                reason="This action requires explicit confirmation.",
                confidence=result.confidence,
                requires_confirmation=True,
                intent=result.intent,
                entities=result.entities,
                metadata={"confirmation_required": True},
            ))

        decision = self._route(result, context)
        return self._record(decision)

    def _route(self, result: IntentResult, context: Dict[str, Any]) -> Decision:
        intent = result.intent
        entities = dict(result.entities)
        metadata: Dict[str, Any] = {"source": "intent_classifier"}

        if intent in {"greeting", "farewell", "conversation"}:
            return Decision(
                ActionType.RESPOND,
                Route.CONVERSATION,
                "Conversational intent should be handled as a response.",
                result.confidence,
                intent=intent,
                entities=entities,
                metadata=metadata,
            )

        if intent == "question":
            return Decision(
                ActionType.RESPOND,
                Route.BRAIN,
                "A knowledge or reasoning response is required.",
                result.confidence,
                intent=intent,
                entities=entities,
                metadata=metadata,
            )

        if intent == "reminder":
            return Decision(
                ActionType.SCHEDULE,
                Route.SCHEDULER,
                "Reminder intent must be handed to the scheduler.",
                result.confidence,
                intent=intent,
                entities=entities,
                metadata=metadata,
            )

        if intent in {"system_control", "file_operation", "app_control"}:
            return Decision(
                ActionType.EXECUTE,
                Route.COMMAND_BUS,
                "Deterministic OS/application action should be sent to the Command Bus.",
                result.confidence,
                intent=intent,
                entities=entities,
                metadata=metadata,
            )

        if intent in {"complex_task", "agent_task", "research", "multi_step"}:
            return Decision(
                ActionType.DELEGATE,
                Route.AGENT,
                "The request requires multi-step planning or verification.",
                result.confidence,
                intent=intent,
                entities=entities,
                metadata=metadata,
            )

        # If the intent is known but has no deterministic route yet, let the
        # Brain interpret it instead of silently executing anything.
        return Decision(
            ActionType.RESPOND,
            Route.BRAIN,
            "Known intent has no deterministic executor yet; Brain should interpret it.",
            result.confidence,
            intent=intent,
            entities=entities,
            metadata=metadata,
        )

    @staticmethod
    def _coerce_intent(intent: IntentResult | str) -> IntentResult:
        if isinstance(intent, IntentResult):
            return intent
        return IntentResult(intent=intent, confidence=0.5, raw_text=intent)

    @staticmethod
    def _clarify(result: IntentResult, reason: str) -> Decision:
        return Decision(
            action=ActionType.CLARIFY,
            route=Route.CONVERSATION,
            reason=reason,
            confidence=result.confidence,
            intent=result.intent,
            entities=dict(result.entities),
            metadata={"clarification_required": True},
        )

    def _record(self, decision: Decision) -> Decision:
        record = {"timestamp": datetime.now().isoformat(), "decision": decision.to_dict()}
        self._decision_history.append(record)
        if len(self._decision_history) > 1000:
            del self._decision_history[:-1000]
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish("decision.made", record))
        except RuntimeError:
            pass
        return decision

    def get_recent_decisions(self, limit: int = 10) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        return self._decision_history[-limit:]


_decision_engine: DecisionEngine | None = None


async def get_decision_engine() -> DecisionEngine:
    global _decision_engine
    if _decision_engine is None:
        _decision_engine = DecisionEngine()
        await _decision_engine.initialize()
    return _decision_engine
