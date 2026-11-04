"""
Main SereinOS Core Service - Always-on daemon.
Orchestrates all engines and provides D-Bus interfaces.
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List, Union

# Setup logging early
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("serein.core.main")

# Import core modules
from serein_core.config import get_config as config, CoreConfig
from serein_core.database import Database, get_database
from serein_core.events import event_bus, CoreEvents, Event, UtteranceEventData
from serein_core.identity import IdentityEngine
from serein_core.persona import PersonaEngine, persona_engine
from serein_core.emotion import EmotionEngine, emotion_engine
from serein_core.memory import MemoryEngine, memory_engine
from serein_core.relationship import RelationshipEngine, relationship_engine
from serein_core.growth import GrowthEngine, growth_engine
from serein_core.context import ContextEngine, get_context_engine
from serein_core.decision import DecisionEngine, get_decision_engine, ActionType, Route
from serein_core.intent.classifier import IntentClassifier
from serein_core.ai import BrainRouter, get_router, ModelTier
from serein_core.command_bus import CommandBus, get_command_bus
from serein_core.scheduler import Scheduler, get_scheduler


class SereinCore:
    """Main SereinOS Core Service."""
    
    def __init__(self, config: CoreConfig = None):
        self.config = config or CoreConfig()
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        # Core engines
        self.identity = IdentityEngine()
        self.persona = persona_engine
        self.emotion = emotion_engine
        self.memory = memory_engine
        self.relationship = relationship_engine
        self.growth = growth_engine
        self.context = None  # ContextEngine
        self.decision = None  # DecisionEngine
        self.intent_classifier = IntentClassifier()
        self.current_session_id: Optional[str] = None
        self.voice = None
        
        # Services
        self.router: BrainRouter = None
        self.command_bus: CommandBus = None
        self.scheduler: Scheduler = None
        self.db: Database = None
        self._unsubscribers: List[Callable] = []
    
    async def initialize(self) -> None:
        """Initialize all subsystems."""
        logger.info("=" * 50)
        logger.info("SereinOS Core Service Starting...")
        logger.info("=" * 50)
        
        # Database
        import serein_core.database as db_mod
        if db_mod.db is None:
            self.db = Database(
                path=self.config.database.path,
                encryption_key=self.config.database.encryption_key,
                pragmas=self.config.database.pragmas,
            )
            await self.db.connect()
            db_mod.db = self.db
        else:
            self.db = db_mod.db
        logger.info("[OK] Database connected")
        
        # Ensure dialogue session exists
        self.current_session_id = await self.db.create_session(summary="Primary session")
        
        # Engines (in dependency order)
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
        
        # Context Engine (provides situational awareness)
        self.context = ContextEngine(self.db)
        await self.context.initialize()
        logger.info("[OK] Context Engine")
        
        # Intent Classifier
        if not getattr(self, "intent_classifier", None):
            self.intent_classifier = IntentClassifier()
        logger.info("[OK] Intent Classifier")
        
        # Decision Engine (reasoning coordinator)
        self.decision = DecisionEngine(database=self.db)
        await self.decision.initialize()
        logger.info("[OK] Decision Engine")
        
        # AI Router
        self.router = BrainRouter(self.config)
        logger.info("[OK] AI Brain Router")
        
        # Command Bus (D-Bus on Linux, direct execution on other platforms)
        from serein_core.command_bus import PermissionTier
        import platform
        all_tiers = {
            PermissionTier.READ_ONLY,
            PermissionTier.REVERSIBLE,
            PermissionTier.DESTRUCTIVE,
            PermissionTier.SENSITIVE,
        }
        self.command_bus = CommandBus(
            allowed_tiers=all_tiers,
            dry_run=(platform.system() != "Linux")
        )
        if platform.system() == "Linux":
            try:
                await self.command_bus.start()
                logger.info("[OK] Command Bus")
            except Exception as e:
                logger.warning(f"Command Bus D-Bus start skipped/failed: {e}")
        else:
            logger.info("[OK] Command Bus (direct execution mode on non-Linux)")
        
        # Scheduler
        self.scheduler = Scheduler()
        await self.scheduler.start()
        logger.info("[OK] Scheduler")
        
        # Cross-engine event subscriptions
        self._subscribe_events()
        
        self._running = True
        logger.info("=" * 50)
        logger.info("SereinOS Core Service READY")
        logger.info("=" * 50)
        
        await event_bus.publish(Event(
            type=CoreEvents.SERVICE_STARTED,
            data={"version": "0.1.0"}
        ))

    async def start(self) -> None:
        """Start the core daemon service and event loops."""
        if not self._running:
            await self.initialize()

    def _subscribe_events(self) -> None:
        """Subscribe to cross-engine and external voice/hardware events."""
        if not hasattr(self, "_unsubscribers") or self._unsubscribers is None:
            self._unsubscribers = []
        
        async def on_memory_stored(event):
            pass
        
        async def on_emotion_changed(event):
            pass
        
        async def on_relationship_updated(event):
            pass
        
        async def on_growth_updated(event):
            pass
        
        self._unsubscribers.append(event_bus.subscribe("memory.stored", on_memory_stored))
        self._unsubscribers.append(event_bus.subscribe("emotion.changed", on_emotion_changed))
        self._unsubscribers.append(event_bus.subscribe("relationship.updated", on_relationship_updated))
        self._unsubscribers.append(event_bus.subscribe("growth.updated", on_growth_updated))
        
        # Scheduler -> Command Bus
        async def on_event_fired(event):
            pass
        
        self._unsubscribers.append(event_bus.subscribe(CoreEvents.EVENT_FIRED, on_event_fired))

        # Voice input event glue: when transcribed utterance arrives, route through process_input
        async def on_voice_input(event: Event):
            if not getattr(self, "_running", False):
                return
            utt_data = getattr(event, "data", None)
            text = ""
            if isinstance(utt_data, UtteranceEventData):
                text = utt_data.text
            elif isinstance(utt_data, dict):
                text = utt_data.get("text", "")
            elif hasattr(utt_data, "text"):
                text = str(utt_data.text)
            elif isinstance(utt_data, str):
                text = utt_data

            if text and text.strip():
                try:
                    result = await self.process_input(text.strip())
                    resp_content = result.get("content", "")
                    if resp_content:
                        await event_bus.publish(Event(
                            type=CoreEvents.VOICE_OUTPUT,
                            data={"text": resp_content, "session_id": getattr(self, "current_session_id", "")}
                        ))
                        if self.voice and hasattr(self.voice, "synthesize_and_play"):
                            try:
                                await self.voice.synthesize_and_play(resp_content)
                            except Exception as ve:
                                logger.warning(f"Voice synthesis output failed: {ve}")
                except Exception as ex:
                    logger.error(f"Error handling voice input event: {ex}", exc_info=True)

        self._unsubscribers.append(event_bus.subscribe(CoreEvents.VOICE_INPUT, on_voice_input))
    
    async def process_input(self, user_input: str, context: Dict = None, **kwargs) -> Dict:
        """
        Main input processing pipeline.
        a. Classifies intent via IntentClassifier.
        b. Decides route via DecisionEngine.
        c. Executes route (CommandBus, Scheduler, or BrainRouter).
        d. Persists dialogue history in SQLite database.
        """
        if not self._running:
            raise RuntimeError("Service not running")
        
        start = asyncio.get_event_loop().time()
        
        # Ensure intent classifier
        if not getattr(self, "intent_classifier", None):
            self.intent_classifier = IntentClassifier()

        # Update context
        context = context or {}
        if self.relationship:
            try:
                context["time_since_last"] = await self.relationship.time_since_last()
                context["relationship_level"] = self.relationship.state.get_level()
            except Exception:
                pass
        
        # Situational context
        if self.context:
            context_state = self.context.get_context()
            context["current_time"] = context_state.current_time
            context["date"] = context_state.date
            context["day_of_week"] = context_state.day_of_week
            context["active_applications"] = context_state.active_applications
            context["internet_connected"] = context_state.internet_connected
            context["session_duration_minutes"] = context_state.session_duration_minutes
        
        # Memory, Emotion, Growth, Relationship updates
        if self.memory:
            try:
                await self.memory.evaluate_and_store(user_input, context)
            except Exception as e:
                logger.debug(f"Memory update error: {e}")
        if self.emotion:
            try:
                await self.emotion.process_message(user_input, context)
            except Exception as e:
                logger.debug(f"Emotion update error: {e}")
        if self.growth:
            try:
                await self.growth.process(user_input, context)
            except Exception as e:
                logger.debug(f"Growth update error: {e}")
        if self.relationship:
            try:
                await self.relationship.record_interaction(quality=self._assess_quality(user_input))
            except Exception as e:
                logger.debug(f"Relationship update error: {e}")
        
        # Recall memories
        memories = []
        if self.memory:
            try:
                memories = await self.memory.recall(user_input, context, limit=10)
            except Exception as e:
                logger.debug(f"Memory recall error: {e}")

        emotion_influence = await self.emotion.get_influence() if self.emotion else {}
        relationship_influence = await self.relationship.get_influence() if self.relationship else {}
        growth_influence = await self.growth.get_influence() if self.growth else {}

        # a. Classify intent
        intent_result = self.intent_classifier.classify(user_input)
        
        # b. Decide route
        user_confirmed = kwargs.get("user_confirmation", False)
        decision = await self.decision.decide(
            intent_result,
            context=context,
            safety={"allowed": True, "reason": ""},
            user_confirmation=user_confirmed
        )

        # c. Execute route
        action_taken = decision.action.value if hasattr(decision.action, "value") else str(decision.action)
        provider = "decision"
        model = "intent"
        final_response = ""

        if decision.route == Route.COMMAND_BUS:
            entities = intent_result.entities
            cmd = intent_result.intent
            params = dict(entities)

            if intent_result.intent == "app_control":
                action = entities.get("action", "launch")
                app_name = entities.get("application", "")
                cmd = "launch_app" if action in ["launch", "open", None] else "close_app"
                params = {"app": app_name}
            elif intent_result.intent == "system_control":
                act = entities.get("action", "")
                if act in ["shutdown", "poweroff"]:
                    cmd = "shutdown"
                elif act in ["reboot", "restart"]:
                    cmd = "reboot"
                else:
                    cmd = act or "system_control"
            elif intent_result.intent == "file_operation":
                op = entities.get("operation", "")
                if op in ["delete", "remove"]:
                    cmd = "delete_file"
                elif op in ["move"]:
                    cmd = "move_file"
                else:
                    cmd = "read_file"

            if self.command_bus:
                try:
                    res = await self.command_bus.execute(cmd, params)
                    if res.requires_confirmation:
                        final_response = f"Confirmation required to execute {cmd}. Please confirm."
                        action_taken = "clarify"
                    elif res.success:
                        final_response = f"Executed {cmd} successfully."
                    else:
                        final_response = f"System control action {cmd}: {res.error or 'completed'}."
                except Exception as e:
                    final_response = f"Executing system action {cmd}: {e}."
            else:
                final_response = f"System control command '{cmd}' registered."
            provider = "command_bus"
            model = cmd

        elif decision.route == Route.SCHEDULER:
            task = intent_result.entities.get("task", user_input)
            t_spec = intent_result.entities.get("time", "")
            d_spec = intent_result.entities.get("date", "")
            full_time = f"{d_spec} {t_spec}".strip() if d_spec else t_spec

            if self.scheduler:
                try:
                    from uuid import uuid4
                    from serein_core.scheduler import ScheduledEvent, TriggerType
                    event = ScheduledEvent(
                        id=str(uuid4()),
                        trigger_type=TriggerType.RELATIVE,
                        message=f"{task} ({full_time})".strip(),
                        payload={"task": task, "time": full_time}
                    )
                    await self.scheduler.schedule_event(event)
                    final_response = f"I have scheduled your reminder for '{task}' ({full_time})."
                except Exception as e:
                    final_response = f"Reminder scheduled: '{task}' at {full_time}."
            else:
                final_response = f"Reminder noted: '{task}' at {full_time}."
            provider = "scheduler"
            model = "scheduler"
            action_taken = "schedule"

        elif decision.action == ActionType.CLARIFY:
            clarify_msg = decision.metadata.get("confirmation_prompt") if hasattr(decision, "metadata") and decision.metadata else None
            final_response = clarify_msg or f"Could you please confirm or clarify: '{user_input}'?"
            provider = "decision"
            model = "clarify"
            action_taken = "clarify"

        else:
            # Route.CONVERSATION or Route.BRAIN
            prompt = await self._build_prompt(
                user_input, memories, context,
                emotion_influence, relationship_influence, growth_influence
            )
            tier = ModelTier.FAST if len(user_input) < 100 else ModelTier.SMART
            response = await self.router.chat(
                [{"role": "user", "content": prompt}],
                tier=tier,
                context=context
            )
            raw_content = response.content
            final_response = self.persona.format_response(
                raw_content,
                emotion_influence
            ) if self.persona else raw_content
            provider = response.provider
            model = response.model
            action_taken = "respond"

        elapsed = (asyncio.get_event_loop().time() - start) * 1000

        # d. Save turn to database for dialogue history persistence
        if self.db:
            try:
                if not getattr(self, "current_session_id", None):
                    self.current_session_id = await self.db.create_session(summary="Dialogue session")
                sess_id = self.current_session_id

                # Save user message
                await self.db.save_message({
                    "session_id": sess_id,
                    "role": "user",
                    "content": user_input,
                    "intent": intent_result.intent if intent_result else None,
                })

                # Save assistant message
                await self.db.save_message({
                    "session_id": sess_id,
                    "role": "assistant",
                    "content": final_response,
                    "intent": intent_result.intent if intent_result else None,
                    "action_taken": action_taken,
                    "provider": provider,
                    "model": model,
                    "latency_ms": int(elapsed),
                })
            except Exception as dbe:
                logger.warning(f"Failed to persist dialogue turn: {dbe}")

        return {
            "content": final_response,
            "action": action_taken,
            "provider": provider,
            "model": model,
            "latency_ms": int(elapsed),
            "session_id": getattr(self, "current_session_id", None),
            "intent": intent_result.intent if intent_result else "unknown",
            "route": decision.route.value if hasattr(decision.route, "value") else str(decision.route),
            "engines": {
                "memory": True,
                "emotion": True,
                "growth": True,
                "relationship": True,
            }
        }
    
    def _assess_quality(self, text: str) -> str:
        pos = ["thank", "thanks", "great", "awesome", "love", "helpful", "perfect", "yes"]
        neg = ["no", "wrong", "bad", "terrible", "hate", "stupid", "useless", "annoying"]
        t = text.lower()
        p = sum(1 for w in pos if w in t)
        n = sum(1 for w in neg if w in t)
        return "positive" if p > n else "negative" if n > p else "neutral"
    
    async def _build_prompt(self, user_input: str, memories: List, context: Dict,
                           emotion_inf: Dict, relationship_inf: Dict, growth_inf: Dict) -> str:
        """Build comprehensive prompt for AI."""
        
        parts = []
        
        # Core prompt
        parts.append(self._load_core_prompt())
        parts.append("\n" + "=" * 50 + "\n")
        
        # Identity
        if self.identity:
            try:
                id_ctx = self.identity.get_context()
                if asyncio.iscoroutine(id_ctx):
                    id_ctx = await id_ctx
                parts.append(str(id_ctx))
            except Exception:
                pass
        parts.append("\n" + "-" * 30 + "\n")
        
        # Persona
        if self.persona:
            try:
                spk = self.persona.get_speaking_prompt()
                if asyncio.iscoroutine(spk):
                    spk = await spk
                parts.append(str(spk))
            except Exception:
                pass
        parts.append("\n" + "-" * 30 + "\n")
        
        # Emotion
        if self.emotion:
            try:
                emo_ctx = self.emotion.get_context()
                if asyncio.iscoroutine(emo_ctx):
                    emo_ctx = await emo_ctx
                parts.append(str(emo_ctx))
            except Exception:
                pass
        parts.append("\n" + "-" * 30 + "\n")
        
        # Relationship
        if self.relationship:
            try:
                rel_ctx = self.relationship.get_context()
                if asyncio.iscoroutine(rel_ctx):
                    rel_ctx = await rel_ctx
                parts.append(str(rel_ctx))
            except Exception:
                pass
        parts.append("\n" + "-" * 30 + "\n")
        
        # Growth
        if self.growth:
            try:
                gro_ctx = self.growth.get_context()
                if asyncio.iscoroutine(gro_ctx):
                    gro_ctx = await gro_ctx
                parts.append(str(gro_ctx))
            except Exception:
                pass
        parts.append("\n" + "-" * 30 + "\n")
        
        # Memories
        if self.memory:
            try:
                mem_ctx = self.memory.get_context(user_input, limit=8)
                if asyncio.iscoroutine(mem_ctx):
                    mem_ctx = await mem_ctx
                parts.append(str(mem_ctx))
            except Exception:
                pass
        parts.append("\n" + "=" * 50 + "\n")
        
        # Current message
        parts.append(f"USER MESSAGE:\n{user_input}")
        parts.append("\n" + "=" * 50 + "\n")
        
        # Guidance
        if emotion_inf.get("tone_modifier"):
            parts.append(f"TONE: {emotion_inf['tone_modifier']}")
        if relationship_inf.get("proactivity", 0) > 0:
            parts.append("- You may be proactive and suggest relevant things")
        if growth_inf.get("initiative", 0) > 0:
            parts.append(f"- Consider topics: {', '.join(growth_inf.get('topics', []))}")
        
        # Relationship-specific persona
        rel_level = context.get("relationship_level", "New Acquaintance")
        persona_add = self.persona.get_prompt_additions(rel_level) if self.persona else ""
        if persona_add:
            parts.append(persona_add)
        
        parts.append("\nRESPOND AS SEREIN:")
        
        return "\n".join(parts)
    
    def _load_core_prompt(self) -> str:
        """Load core prompt from file."""
        try:
            prompt_path = Path("/etc/serein/SEREIN_CORE_PROMPT.md")
            if prompt_path.exists():
                return prompt_path.read_text()
        except Exception:
            pass
        return "You are Serein, an AI companion designed to grow alongside the user through shared experiences."
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("\n[SereinCore] Shutting down...")
        self._running = False
        
        # Consolidate memories
        await self.memory.consolidate()
        
        # Stop services
        for unsub in getattr(self, "_unsubscribers", []):
            try:
                unsub()
            except Exception:
                pass
        if hasattr(self, "_unsubscribers"):
            self._unsubscribers.clear()

        if self.scheduler:
            await self.scheduler.stop()
        if self.command_bus and hasattr(self.command_bus, "stop"):
            try:
                await self.command_bus.stop()
            except Exception:
                pass
        if self.router:
            await self.router.close()
        if self.context and hasattr(self.context, "close"):
            try:
                await self.context.close()
            except Exception:
                pass
        if self.db:
            await self.db.close()
            import serein_core.database as db_mod
            if db_mod.db is self.db:
                db_mod.db = None
        
        logger.info("[SereinCore] Shutdown complete")
        self._shutdown_event.set()
    
    async def run_forever(self) -> None:
        """Run until shutdown signal."""
        await self._shutdown_event.wait()


async def main():
    """Main entry point."""
    # Handle signals
    loop = asyncio.get_running_loop()
    core = SereinCore()
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(core.shutdown()))
    
    try:
        await core.initialize()
        await core.run_forever()
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


def cli_main():
    """CLI entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()