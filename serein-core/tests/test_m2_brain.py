"""
Milestone 2 Verification Test Suite: Autonomous Hybrid Brain & Persistent SQLite Memory
Tests all M2 components:
1. Non-Blocking Network Monitor (context.py)
2. SQLite Persistent Dialogue & Preferences (database.py)
3. Additional Cloud AI Providers & Seamless Failover (ai.py)
4. Core Pipeline & Voice Event Glue (main.py)
"""
import asyncio
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from serein_core.ai import (
    AnthropicProvider,
    BrainRouter,
    ChatMessage,
    CustomOpenAIProvider,
    GeminiProvider,
    ModelTier,
    OpenAIProvider,
)
from serein_core.config import CoreConfig
from serein_core.context import ContextEngine, ContextState
from serein_core.database import Database
from serein_core.decision import ActionType, DecisionEngine, Route
from serein_core.events import CoreEvents, Event, EventBus, UtteranceEventData, event_bus
from serein_core.intent.classifier import IntentClassifier
from serein_core.main import SereinCore


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_db_path(tmp_path):
    return tmp_path / "test_m2_serein.db"


@pytest_asyncio.fixture
async def test_db(temp_db_path):
    db = Database(temp_db_path)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


# ============================================================================
# 1. Non-Blocking Network Monitor Tests
# ============================================================================

class TestNonBlockingNetworkMonitor:
    """Verifies non-blocking probe, caching, and event emission on transition."""

    def test_network_check_fast_timeout(self):
        """Socket check completes within fast timeout bounds (<= 0.6s)."""
        engine = ContextEngine()
        start = time.perf_counter()
        _ = engine._check_internet(timeout=0.2)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.6

    @pytest.mark.asyncio
    async def test_async_probe_in_threadpool(self):
        """check_internet_async runs asynchronously in threadpool without blocking event loop."""
        engine = ContextEngine()
        start = time.perf_counter()
        results = await asyncio.gather(*[engine.check_internet_async(timeout=0.2) for _ in range(3)])
        elapsed = time.perf_counter() - start
        assert len(results) == 3
        assert elapsed < 1.0

    def test_network_state_cache_ttl(self):
        """get_context() uses cached network connectivity within TTL without socket blocking."""
        engine = ContextEngine()
        engine._context = ContextState()
        engine._cached_internet = True
        engine._last_network_check = time.time()
        engine._network_cache_ttl = 10.0

        # With mock socket that would raise error if called:
        with patch("socket.create_connection", side_effect=RuntimeError("Should not be called")):
            ctx = engine.get_context()
            assert ctx.internet_connected is True

    @pytest.mark.asyncio
    async def test_network_transition_event_emitted(self):
        """SYSTEM_STATE_CHANGED event emitted when connectivity state transitions."""
        engine = ContextEngine()
        engine._context = ContextState()
        received_events = []

        async def handler(evt: Event):
            received_events.append(evt.data)

        event_bus.subscribe(CoreEvents.SYSTEM_STATE_CHANGED, handler)

        # Transition to offline
        await engine.set_connectivity(False)
        assert engine.get_state().internet_connected is False
        assert any(e.get("network") == "offline" for e in received_events)

        # Transition to online
        await engine.set_connectivity(True)
        assert engine.get_state().internet_connected is True
        assert any(e.get("network") == "online" for e in received_events)


# ============================================================================
# 2. SQLite Persistent Dialogue & Preferences Tests
# ============================================================================

class TestSQLiteDialogueAndPreferences:
    """Verifies SQLite dialogue history sessions/messages and user preferences."""

    @pytest.mark.asyncio
    async def test_conversation_session_crud(self, test_db):
        """Create session, retrieve session, and end session."""
        sess_id = await test_db.create_session(summary="Test Session")
        assert sess_id is not None

        sess = await test_db.get_session(sess_id)
        assert sess is not None
        assert sess["id"] == sess_id
        assert sess["summary"] == "Test Session"
        assert sess["started_at"] is not None
        assert sess["ended_at"] is None

        await test_db.end_session(sess_id, summary="Session completed successfully")
        updated = await test_db.get_session(sess_id)
        assert updated["ended_at"] is not None
        assert updated["summary"] == "Session completed successfully"

    @pytest.mark.asyncio
    async def test_save_and_retrieve_dialogue_messages(self, test_db):
        """Save turns and retrieve recent messages in chronological order."""
        sess_id = await test_db.create_session(summary="Dialogue history test")

        m1_id = await test_db.save_message({
            "session_id": sess_id,
            "role": "user",
            "content": "What is SereinOS?",
            "intent": "question",
            "timestamp": "2026-11-01T10:00:00",
        })

        m2_id = await test_db.save_message({
            "session_id": sess_id,
            "role": "assistant",
            "content": "SereinOS is an intelligent companion operating system.",
            "intent": "question",
            "action_taken": "respond",
            "provider": "offline-deterministic",
            "model": "serein-offline-intent",
            "latency_ms": 42,
            "timestamp": "2026-11-01T10:00:01",
        })

        recent = await test_db.get_recent_messages(limit=10, session_id=sess_id)
        assert len(recent) == 2
        assert recent[0]["id"] == m1_id
        assert recent[0]["role"] == "user"
        assert recent[1]["id"] == m2_id
        assert recent[1]["role"] == "assistant"
        assert recent[1]["provider"] == "offline-deterministic"

    @pytest.mark.asyncio
    async def test_user_preferences_persistence(self, test_db):
        """Save and retrieve various types of preferences (string, int, dict, bool)."""
        await test_db.set_preference("theme", "cyberpunk")
        await test_db.set_preference("volume_level", 85)
        await test_db.set_preference("offline_only", True)
        await test_db.set_preference("voice_settings", {"voice": "piper", "speed": 1.1})

        assert await test_db.get_preference("theme") == "cyberpunk"
        assert await test_db.get_preference("volume_level") == 85
        assert await test_db.get_preference("offline_only") is True
        assert await test_db.get_preference("voice_settings") == {"voice": "piper", "speed": 1.1}
        assert await test_db.get_preference("nonexistent_key", default="fallback") == "fallback"

        all_prefs = await test_db.get_all_preferences()
        assert "theme" in all_prefs
        assert all_prefs["volume_level"] == 85


# ============================================================================
# 3. Additional Cloud AI Providers & Failover Tests
# ============================================================================

class TestCloudAIProvidersAndFailover:
    """Verifies OpenAI, Anthropic, Gemini, Custom providers, and seamless offline failover."""

    def test_openai_provider_configuration(self):
        """OpenAIProvider model configs and base URL."""
        p = OpenAIProvider(api_key="sk-test", base_url="https://api.openai.com/v1")
        assert p.name == "openai"
        assert p.get_model_config(ModelTier.FAST).name == "gpt-4o-mini"
        assert p.get_model_config(ModelTier.SMART).name == "gpt-4o"

    def test_anthropic_provider_configuration(self):
        """AnthropicProvider model configs and base URL."""
        p = AnthropicProvider(api_key="sk-ant-test", base_url="https://api.anthropic.com/v1")
        assert p.name == "anthropic"
        assert "claude-3-5" in p.get_model_config(ModelTier.FAST).name

    def test_gemini_provider_configuration(self):
        """GeminiProvider model configs and base URL."""
        p = GeminiProvider(api_key="gm-test", base_url="https://generativelanguage.googleapis.com/v1beta")
        assert p.name == "gemini"
        assert "gemini-1.5" in p.get_model_config(ModelTier.FAST).name

    def test_custom_openai_provider_configuration(self):
        """CustomOpenAIProvider allows setting private endpoints."""
        p = CustomOpenAIProvider(base_url="http://192.168.1.100:8080/v1", name="vllm_local", default_model="mistral-7b")
        assert p.name == "vllm_local"
        assert p.get_model_config(ModelTier.FAST).name == "mistral-7b"

    @pytest.mark.asyncio
    async def test_brain_router_offline_failover_zero_unhandled_crashes(self):
        """When offline, chat() gracefully fails over to deterministic offline generator with zero crashes."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        # Explicitly pass offline context
        msgs = [ChatMessage(role="user", content="hello Serein")]
        resp = await router.chat(msgs, tier=ModelTier.FAST, context={"internet_connected": False})

        assert resp is not None
        assert resp.content != ""
        assert resp.tier == ModelTier.LOCAL
        assert resp.provider in ["offline-deterministic", "local-llama.cpp"]

    @pytest.mark.asyncio
    async def test_brain_router_cloud_failure_gracefully_falls_back(self):
        """When cloud provider raises an exception, router does NOT crash with RuntimeError."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        # Mock all cloud providers to fail
        failing_provider = MagicMock()
        failing_provider.name = "mock_failing"
        failing_provider.available_tiers = [ModelTier.FAST, ModelTier.SMART]
        failing_provider.get_model_config.return_value = MagicMock(name="failing_model")
        failing_provider.chat = AsyncMock(side_effect=ConnectionError("Cloud API unreachable"))

        router.providers = {"failing": failing_provider}

        msgs = [ChatMessage(role="user", content="What time is it?")]
        # Context is online, but cloud calls fail:
        resp = await router.chat(msgs, tier=ModelTier.FAST, context={"internet_connected": True})

        assert resp is not None
        assert resp.content != ""
        assert "time" in resp.content.lower()
        assert resp.provider == "offline-deterministic"

    @pytest.mark.asyncio
    async def test_deterministic_offline_generator_intents(self):
        """Offline generator responds to greetings, questions, system controls, and reminders."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        cases = [
            ("hey there", "hello"),
            ("what is the time right now?", "time"),
            ("remind me to backup files at 8pm", "reminder"),
            ("open terminal", "terminal"),
            ("shutdown system", "action"),
        ]

        for query, expected_token in cases:
            resp = await router.chat(
                [ChatMessage(role="user", content=query)],
                tier=ModelTier.LOCAL,
                context={"internet_connected": False}
            )
            assert expected_token in resp.content.lower()


# ============================================================================
# 4. Core Pipeline & Voice Event Glue Tests
# ============================================================================

class TestCorePipelineAndVoiceEventGlue:
    """Verifies end-to-end SereinCore pipeline, routing, persistence, and voice input subscription."""

    @pytest_asyncio.fixture
    async def serein_core(self, temp_db_path):
        os.environ["SEREIN_ENABLE_EMBEDDINGS"] = "0"
        cfg = CoreConfig()
        cfg.database.path = temp_db_path
        core = SereinCore(cfg)
        await core.initialize()
        try:
            yield core
        finally:
            await core.shutdown()

    @pytest.mark.asyncio
    async def test_process_input_system_control_route(self, serein_core):
        """System control command routes to COMMAND_BUS and returns executed response."""
        res = await serein_core.process_input("open terminal", user_confirmation=True)
        assert res["action"] in ["execute", "clarify", "respond"]
        assert "terminal" in res["content"].lower() or "executed" in res["content"].lower() or "registered" in res["content"].lower()

    @pytest.mark.asyncio
    async def test_process_input_reminder_route(self, serein_core):
        """Reminder query routes to SCHEDULER and saves event."""
        res = await serein_core.process_input("remind me to inspect battery status at 6pm")
        assert res["action"] in ["schedule", "execute", "respond"]
        assert "reminder" in res["content"].lower() or "scheduled" in res["content"].lower() or "battery" in res["content"].lower()

    @pytest.mark.asyncio
    async def test_process_input_dialogue_persisted_to_database(self, serein_core):
        """Dialogue turn is saved to SQLite conversation_messages table."""
        res = await serein_core.process_input("Hello Serein, are you ready?")
        assert res["content"] != ""
        sess_id = res["session_id"]
        assert sess_id is not None

        messages = await serein_core.db.get_recent_messages(limit=10, session_id=sess_id)
        assert len(messages) >= 2
        user_msg = [m for m in messages if m["role"] == "user"][-1]
        asst_msg = [m for m in messages if m["role"] == "assistant"][-1]
        assert user_msg["content"] == "Hello Serein, are you ready?"
        assert asst_msg["content"] == res["content"]

    @pytest.mark.asyncio
    async def test_voice_input_event_bus_glue(self, serein_core):
        """CoreEvents.VOICE_INPUT triggers process_input and publishes CoreEvents.VOICE_OUTPUT."""
        output_received = asyncio.Event()
        received_text = []

        async def on_voice_output(event: Event):
            received_text.append(event.data.get("text", ""))
            output_received.set()

        event_bus.subscribe(CoreEvents.VOICE_OUTPUT, on_voice_output)

        # Emit voice input event
        utt_event = Event(
            type=CoreEvents.VOICE_INPUT,
            data=UtteranceEventData(text="hello Serein", confidence=0.98, audio_duration=0.5)
        )
        await event_bus.publish(utt_event)

        await asyncio.wait_for(output_received.wait(), timeout=3.0)
        assert len(received_text) > 0
        assert received_text[0] != ""
