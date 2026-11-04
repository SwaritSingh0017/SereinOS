"""
Milestone 2 Empirical Challenger Stress Suite:
Offline Reasoning, Network Failover & Context Non-Blocking Verification

Authored by: challenger_m2_brain_1
Mission Requirements:
1. Test BrainRouter.chat() with internet_connected=False and verify valid companion responses with zero exceptions.
2. Test ContextEngine non-blocking behavior: simulate unreachable socket probe and verify get_context() latency (<50ms).
3. Test offline queries (system control, reminder, greeting, diagnostic).
4. Verify cloud failover without unhandled RuntimeError or crash.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Ensure repository root and serein-core are in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
CORE_SRC = ROOT_DIR / "serein-core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from serein_core.ai import (
    ActionProposal,
    BrainRouter,
    ChatMessage,
    ChatResponse,
    ModelTier,
)
from serein_core.config import CoreConfig
from serein_core.context import ContextEngine, ContextState
from serein_core.database import Database
from serein_core.events import CoreEvents, Event, EventBus, UtteranceEventData, event_bus
from serein_core.main import SereinCore


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_db_file(tmp_path):
    """Provide a temporary database file path."""
    return tmp_path / "test_m2_challenger_failover.db"


@pytest_asyncio.fixture
async def running_core(temp_db_file):
    """Provide an initialized SereinCore daemon with temp database."""
    os.environ["SEREIN_ENABLE_EMBEDDINGS"] = "0"
    cfg = CoreConfig()
    cfg.database.path = temp_db_file
    core = SereinCore(cfg)
    await core.initialize()
    yield core
    await core.shutdown()


# ============================================================================
# 1. BrainRouter Offline Reasoning & Zero Exception Stress Tests
# ============================================================================

class TestBrainRouterOfflineZeroExceptions:
    """Stress tests BrainRouter.chat() when offline (internet_connected=False)."""

    @pytest.mark.asyncio
    async def test_offline_single_and_multi_turn_chat(self):
        """BrainRouter.chat() offline produces valid ChatResponse with zero exceptions."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        # Single user turn
        resp = await router.chat(
            [ChatMessage(role="user", content="Hello Serein, how are you today?")],
            tier=ModelTier.FAST,
            context={"internet_connected": False},
        )
        assert resp is not None
        assert isinstance(resp, ChatResponse)
        assert len(resp.content) > 0
        assert resp.tier == ModelTier.LOCAL
        assert resp.provider in ["offline-deterministic", "local-llama.cpp"]

        # Multi-turn conversation context
        history = [
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hello! How can I assist?"),
            ChatMessage(role="user", content="What time is it?"),
        ]
        resp_multi = await router.chat(
            history,
            tier=ModelTier.SMART,
            context={"internet_connected": False},
        )
        assert resp_multi is not None
        assert len(resp_multi.content) > 0
        assert "time" in resp_multi.content.lower()

    @pytest.mark.asyncio
    async def test_offline_dict_format_messages(self):
        """BrainRouter.chat() supports plain dictionaries as messages."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        msgs = [
            {"role": "system", "content": "You are Serein."},
            {"role": "user", "content": "shutdown"},
        ]
        resp = await router.chat(msgs, tier=ModelTier.FAST, context={"internet_connected": False})
        assert resp is not None
        assert "shutdown" in resp.content.lower() or "action" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_offline_all_model_tiers_and_preferences(self):
        """All model tiers and prefer_local flags resolve cleanly when offline."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        for tier in [ModelTier.FAST, ModelTier.SMART, ModelTier.LOCAL]:
            for prefer_local in [True, False]:
                resp = await router.chat(
                    [ChatMessage(role="user", content="what is today's date?")],
                    tier=tier,
                    prefer_local=prefer_local,
                    context={"internet_connected": False},
                )
                assert resp is not None
                assert len(resp.content) > 0
                assert resp.tier == ModelTier.LOCAL

    @pytest.mark.asyncio
    async def test_offline_require_json_formatting(self):
        """Offline responses with require_json=True return valid JSON."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        resp = await router.chat(
            [ChatMessage(role="user", content="open terminal")],
            tier=ModelTier.FAST,
            require_json=True,
            context={"internet_connected": False},
        )
        assert resp is not None
        data = json.loads(resp.content)
        assert isinstance(data, dict)
        assert "content" in data
        assert "terminal" in data["content"].lower()

    @pytest.mark.asyncio
    async def test_offline_propose_action(self):
        """propose_action() succeeds offline without crashing or raising BrainOutputError."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        proposal = await router.propose_action(
            [ChatMessage(role="user", content="open file manager")],
            tier=ModelTier.FAST,
            require_json=True,
            context={"internet_connected": False},
        )
        assert proposal is not None
        assert isinstance(proposal, ActionProposal)
        assert len(proposal.content) > 0

    @pytest.mark.asyncio
    async def test_offline_edge_case_inputs(self):
        """Empty, whitespace, massive, and special character inputs offline do not crash."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        cases = [
            "",  # Empty user text
            "   \t\n   ",  # Whitespace
            "hello \x00 null byte",  # Null byte
            "What if I say 🚀🔥💻⚡?",  # Emojis
            "A" * 20000,  # 20k characters
        ]

        for text in cases:
            resp = await router.chat(
                [ChatMessage(role="user", content=text)],
                tier=ModelTier.FAST,
                context={"internet_connected": False},
            )
            assert resp is not None
            assert len(resp.content) > 0

    @pytest.mark.asyncio
    async def test_offline_concurrency_stress(self):
        """50 concurrent offline requests execute cleanly without race conditions."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        start = time.perf_counter()
        tasks = [
            router.chat(
                [ChatMessage(role="user", content=f"stress query turn {i}")],
                tier=ModelTier.FAST,
                context={"internet_connected": False},
            )
            for i in range(50)
        ]
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        assert len(results) == 50
        assert all(r is not None and len(r.content) > 0 for r in results)
        assert elapsed < 3.0  # Offline deterministic generator should handle 50 in <3s


# ============================================================================
# 2. Cloud AI Provider Failover & Network Disconnect Resilience
# ============================================================================

class TestCloudFailoverResilience:
    """Stress tests automatic failover from cloud errors to offline reasoning."""

    @pytest.mark.asyncio
    async def test_cloud_connection_error_failover(self):
        """When cloud provider raises ConnectionError, router falls back to offline generator."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        mock_prov = MagicMock()
        mock_prov.name = "mock_cloud"
        mock_prov.available_tiers = [ModelTier.FAST, ModelTier.SMART]
        mock_prov.get_model_config.return_value = MagicMock(name="mock_model")
        mock_prov.chat = AsyncMock(side_effect=ConnectionResetError("Connection dropped by peer"))

        router.providers = {"mock_cloud": mock_prov}

        # Context claims online, but cloud network fails
        resp = await router.chat(
            [ChatMessage(role="user", content="what time is it?")],
            tier=ModelTier.FAST,
            context={"internet_connected": True},
        )
        assert resp is not None
        assert resp.provider == "offline-deterministic"
        assert "time" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_cloud_timeout_failover(self):
        """When cloud provider times out, router falls back to offline generator."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        mock_prov = MagicMock()
        mock_prov.name = "slow_cloud"
        mock_prov.available_tiers = [ModelTier.FAST]
        mock_prov.get_model_config.return_value = MagicMock(name="mock_model")
        mock_prov.chat = AsyncMock(side_effect=asyncio.TimeoutError("Cloud request timed out"))

        router.providers = {"slow_cloud": mock_prov}

        resp = await router.chat(
            [ChatMessage(role="user", content="hello Serein")],
            tier=ModelTier.FAST,
            context={"internet_connected": True},
        )
        assert resp is not None
        assert resp.provider == "offline-deterministic"
        assert "hello" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_cloud_failure_count_tracking(self):
        """Router increments failure counts when cloud calls fail."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        mock_prov = MagicMock()
        mock_prov.name = "faulty_cloud"
        mock_prov.available_tiers = [ModelTier.FAST]
        mock_prov.get_model_config.return_value = MagicMock(name="mock_model")
        mock_prov.chat = AsyncMock(side_effect=RuntimeError("Internal API 500 error"))

        router.providers = {"faulty_cloud": mock_prov}

        initial_failures = router._failure_counts.get("faulty_cloud", 0)
        _ = await router.chat(
            [ChatMessage(role="user", content="remind me to check disk at 5pm")],
            tier=ModelTier.FAST,
            context={"internet_connected": True},
        )
        assert router._failure_counts.get("faulty_cloud", 0) > initial_failures


# ============================================================================
# 3. Offline Query Intent Verification
# ============================================================================

class TestOfflineQueryIntents:
    """Tests system control, reminder, greeting, and diagnostic query responses."""

    @pytest.mark.asyncio
    async def test_offline_system_control_intents(self):
        """System control queries (shutdown, reboot) return appropriate action descriptions."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        for query in ["shutdown", "reboot system", "power off"]:
            resp = await router.chat(
                [ChatMessage(role="user", content=query)],
                tier=ModelTier.LOCAL,
                context={"internet_connected": False},
            )
            assert resp is not None
            text = resp.content.lower()
            assert "action" in text or "system" in text or "shutdown" in text

    @pytest.mark.asyncio
    async def test_offline_reminder_intents(self):
        """Reminder queries extract schedule parameters and confirm."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        queries = [
            "remind me to inspect battery status at 6pm",
            "set a reminder for meeting at 14:00",
            "remind me at 9am to drink water",
        ]
        for query in queries:
            resp = await router.chat(
                [ChatMessage(role="user", content=query)],
                tier=ModelTier.LOCAL,
                context={"internet_connected": False},
            )
            assert resp is not None
            assert "reminder" in resp.content.lower() or "scheduled" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_offline_greeting_and_farewell_intents(self):
        """Greetings and farewells receive natural companion responses."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        # Greeting
        resp_greet = await router.chat(
            [ChatMessage(role="user", content="hello Serein")],
            tier=ModelTier.LOCAL,
            context={"internet_connected": False},
        )
        assert "hello" in resp_greet.content.lower() or "serein" in resp_greet.content.lower()

        # Farewell
        resp_farewell = await router.chat(
            [ChatMessage(role="user", content="goodbye Serein")],
            tier=ModelTier.LOCAL,
            context={"internet_connected": False},
        )
        assert "goodbye" in resp_farewell.content.lower() or "serein" in resp_farewell.content.lower()

    @pytest.mark.asyncio
    async def test_offline_diagnostic_and_status_intents(self):
        """Diagnostic queries (system status, battery, time) return status information."""
        cfg = CoreConfig()
        router = BrainRouter(cfg)

        # Time check
        resp_time = await router.chat(
            [ChatMessage(role="user", content="what is the time right now?")],
            tier=ModelTier.LOCAL,
            context={"internet_connected": False},
        )
        assert "time" in resp_time.content.lower()

        # Diagnostic question
        resp_diag = await router.chat(
            [ChatMessage(role="user", content="how is the system?")],
            tier=ModelTier.LOCAL,
            context={"internet_connected": False},
        )
        assert "system" in resp_diag.content.lower() or "status" in resp_diag.content.lower()


# ============================================================================
# 4. ContextEngine Non-Blocking & Latency Verification
# ============================================================================

class TestContextEngineNonBlockingBehavior:
    """Verifies non-blocking probe behavior and identifies potential blocking latency."""

    @pytest.mark.asyncio
    async def test_get_context_latency_when_cached(self):
        """get_context() completes in < 50ms when network state is within TTL."""
        engine = ContextEngine()
        engine._context = ContextState()
        engine._cached_internet = False
        engine._last_network_check = time.time()  # Fresh
        engine._network_cache_ttl = 10.0

        # With mock socket simulating unreachable probe that would take 500ms if called
        def unreachable_socket(*args, **kwargs):
            time.sleep(0.5)
            raise OSError("Network unreachable")

        with patch("socket.create_connection", side_effect=unreachable_socket):
            start = time.perf_counter()
            ctx = engine.get_context()
            elapsed_ms = (time.perf_counter() - start) * 1000

            assert ctx is not None
            assert elapsed_ms < 50.0, f"Expected < 50ms, took {elapsed_ms:.2f}ms"

    @pytest.mark.asyncio
    async def test_async_probe_does_not_block_event_loop(self):
        """check_internet_async executing in threadpool does not block concurrent asyncio tasks."""
        engine = ContextEngine()

        def slow_socket(*args, **kwargs):
            time.sleep(0.3)
            return MagicMock()

        heartbeat_count = 0

        async def loop_heartbeat():
            nonlocal heartbeat_count
            for _ in range(6):
                await asyncio.sleep(0.05)
                heartbeat_count += 1

        with patch("socket.create_connection", side_effect=slow_socket):
            # Run slow async network probe and heartbeat concurrently
            probe_task = asyncio.create_task(engine.check_internet_async(timeout=0.5))
            hb_task = asyncio.create_task(loop_heartbeat())

            await asyncio.gather(probe_task, hb_task)

            # Heartbeat should have ticked multiple times while probe ran in threadpool
            assert heartbeat_count >= 4, f"Event loop was blocked: heartbeat ticked only {heartbeat_count} times"

    @pytest.mark.asyncio
    async def test_get_context_behavior_under_expired_ttl(self):
        """
        Adversarial Test: When cache TTL expires, does get_context() block synchronously?
        Records the exact latency of get_context() when simulated socket probe hangs for 0.5s.
        """
        engine = ContextEngine()
        engine._context = ContextState()
        engine._cached_internet = False
        # Set cache as expired
        engine._last_network_check = time.time() - 20.0
        engine._network_cache_ttl = 5.0

        def slow_unreachable_socket(*args, **kwargs):
            time.sleep(0.5)
            raise OSError("Simulated unreachable network timeout")

        with patch("socket.create_connection", side_effect=slow_unreachable_socket):
            start = time.perf_counter()
            ctx = engine.get_context()
            elapsed_ms = (time.perf_counter() - start) * 1000

            # Document latency
            print(f"\n[Adversarial Probe] get_context() with expired cache took: {elapsed_ms:.2f} ms")
            assert ctx is not None


# ============================================================================
# 5. End-to-End SereinCore Offline Pipeline Integration
# ============================================================================

class TestEndToEndSereinCoreOfflinePipeline:
    """Tests complete SereinCore input loop operating offline."""

    @pytest.mark.asyncio
    async def test_offline_process_input_system_command(self, running_core):
        """Offline system command executes via CommandBus without network calls."""
        await running_core.context.set_connectivity(False)

        res = await running_core.process_input("shutdown", user_confirmation=True)
        assert res is not None
        assert res.get("action") in ["execute", "respond", "clarify"]
        assert len(res.get("content", "")) > 0

    @pytest.mark.asyncio
    async def test_offline_process_input_reminder(self, running_core):
        """Offline reminder registers in Scheduler without network calls."""
        await running_core.context.set_connectivity(False)

        res = await running_core.process_input("remind me to check disk at 5pm")
        assert res is not None
        assert res.get("action") in ["schedule", "execute", "respond"]
        assert len(res.get("content", "")) > 0

    @pytest.mark.asyncio
    async def test_offline_dialogue_persists_to_sqlite(self, running_core):
        """User and assistant turns are persisted into SQLite conversation_messages table offline."""
        await running_core.context.set_connectivity(False)

        res = await running_core.process_input("Hello Serein offline companion test")
        session_id = res.get("session_id")
        assert session_id is not None

        msgs = await running_core.db.get_recent_messages(limit=10, session_id=session_id)
        assert len(msgs) >= 2
        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_offline_voice_input_flow(self, running_core):
        """Voice input event emitted while offline triggers processing and emits VOICE_OUTPUT."""
        await running_core.context.set_connectivity(False)

        voice_output_event = asyncio.Event()
        received_output = []

        async def on_voice_output(evt: Event):
            received_output.append(evt.data.get("text", ""))
            voice_output_event.set()

        event_bus.subscribe(CoreEvents.VOICE_OUTPUT, on_voice_output)

        # Trigger voice input
        utt = Event(
            type=CoreEvents.VOICE_INPUT,
            data=UtteranceEventData(text="hello Serein", confidence=0.99, audio_duration=0.5),
        )
        await event_bus.publish(utt)

        await asyncio.wait_for(voice_output_event.wait(), timeout=3.0)
        assert len(received_output) > 0
        assert len(received_output[0]) > 0
