"""
Milestone 2 Empirical Challenger Stress Suite:
SQLite Persistence & Voice Event Integration Stress Harness

Authored by: challenger_m2_brain_2
Mission:
1. Multi-turn dialogue persistence across multiple sessions (conversation_sessions, conversation_messages).
2. User preferences persistence across database re-instantiations.
3. CoreEvents.VOICE_INPUT dispatch: publish UtteranceEventData, verify SereinCore.process_input() executes and emits CoreEvents.VOICE_OUTPUT.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

import pytest
import pytest_asyncio

# Ensure repository root and serein-core are in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
CORE_SRC = ROOT_DIR / "serein-core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from serein_core.config import CoreConfig
from serein_core.database import Database
from serein_core.events import CoreEvents, Event, EventBus, UtteranceEventData, event_bus
from serein_core.main import SereinCore


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_db_file(tmp_path):
    """Provide a path to a clean temporary SQLite database file."""
    db_file = tmp_path / "stress_challenger_m2.db"
    return db_file


@pytest_asyncio.fixture
async def active_db(temp_db_file):
    """Provide an active connected Database instance and ensure clean close."""
    db = Database(temp_db_file)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@pytest_asyncio.fixture
async def running_core(temp_db_file):
    """Provide an initialized and running SereinCore daemon with temp database."""
    os.environ["SEREIN_ENABLE_EMBEDDINGS"] = "0"
    cfg = CoreConfig()
    cfg.database.path = temp_db_file
    core = SereinCore(cfg)
    await core.initialize()
    try:
        yield core
    finally:
        await core.shutdown()


# ============================================================================
# 1. Multi-Turn Dialogue Persistence Across Multiple Sessions
# ============================================================================

class TestMultiTurnDialoguePersistenceStress:
    """Stress tests verifying conversation_sessions and conversation_messages tables."""

    @pytest.mark.asyncio
    async def test_multisession_isolation_and_interleaved_turns(self, active_db):
        """
        Create 5 separate sessions and write interleaved multi-turn dialogues across them.
        Verify absolute session isolation and chronological turn order per session.
        """
        session_ids = [
            await active_db.create_session(summary=f"Stress Session {i}")
            for i in range(5)
        ]

        # 10 turns (20 messages: user + assistant) per session = 100 messages total
        # Interleave writes across sessions to stress concurrent indexing
        total_turns_per_session = 10
        expected_user_msgs: Dict[str, List[str]] = {s_id: [] for s_id in session_ids}
        expected_asst_msgs: Dict[str, List[str]] = {s_id: [] for s_id in session_ids}

        for turn_idx in range(total_turns_per_session):
            for s_idx, s_id in enumerate(session_ids):
                u_text = f"User Turn {turn_idx} in Session {s_idx}"
                a_text = f"Assistant Response {turn_idx} in Session {s_idx}"
                expected_user_msgs[s_id].append(u_text)
                expected_asst_msgs[s_id].append(a_text)

                t_user = f"2026-11-01T12:{turn_idx:02d}:{s_idx*2:02d}.000"
                t_asst = f"2026-11-01T12:{turn_idx:02d}:{s_idx*2 + 1:02d}.500"

                await active_db.save_message({
                    "session_id": s_id,
                    "role": "user",
                    "content": u_text,
                    "intent": "chat",
                    "timestamp": t_user,
                })
                await active_db.save_message({
                    "session_id": s_id,
                    "role": "assistant",
                    "content": a_text,
                    "intent": "chat",
                    "action_taken": "respond",
                    "provider": "offline-deterministic",
                    "model": "intent-v1",
                    "latency_ms": 25,
                    "timestamp": t_asst,
                })

        # Verify each session independently
        for s_idx, s_id in enumerate(session_ids):
            messages = await active_db.get_recent_messages(limit=50, session_id=s_id)
            assert len(messages) == 20, f"Session {s_id} should have exactly 20 messages"

            # Check that all messages in this session belong strictly to this session
            for msg in messages:
                assert msg["session_id"] == s_id

            # Verify strictly alternating roles
            roles = [m["role"] for m in messages]
            assert roles == ["user", "assistant"] * total_turns_per_session

            # Verify contents match exact chronological sequence
            actual_user = [m["content"] for m in messages if m["role"] == "user"]
            actual_asst = [m["content"] for m in messages if m["role"] == "assistant"]
            assert actual_user == expected_user_msgs[s_id]
            assert actual_asst == expected_asst_msgs[s_id]

    @pytest.mark.asyncio
    async def test_multisession_persistence_across_database_reinstantiations(self, temp_db_file):
        """
        Create sessions and dialogue turns in Database Instance 1.
        Close Instance 1.
        Open Database Instance 2 on same database file and verify sessions and messages persist.
        """
        db1 = Database(temp_db_file)
        await db1.connect()
        s_id = await db1.create_session(summary="Persistent Session across instances")
        m_id = await db1.save_message({
            "session_id": s_id,
            "role": "user",
            "content": "Will this dialogue persist across DB re-instantiations?",
            "intent": "question",
            "timestamp": "2026-11-01T12:00:00",
        })
        await db1.close()
        del db1

        # Instance 2
        db2 = Database(temp_db_file)
        await db2.connect()
        sess = await db2.get_session(s_id)
        assert sess is not None, "Session must persist across database re-instantiation"
        assert sess["summary"] == "Persistent Session across instances"

        msgs = await db2.get_recent_messages(limit=10, session_id=s_id)
        assert len(msgs) == 1, "Dialogue message must persist across database re-instantiation"
        assert msgs[0]["id"] == m_id
        assert msgs[0]["content"] == "Will this dialogue persist across DB re-instantiations?"
        await db2.close()

    @pytest.mark.asyncio
    async def test_dialogue_roles_and_metadata_fidelity(self, active_db):
        """
        Verify all metadata fields (intent, action_taken, provider, model, latency_ms, timestamp)
        and roles ('user', 'assistant', 'system') are faithfully preserved.
        """
        s_id = await active_db.create_session(summary="Metadata Fidelity Session")

        test_turns = [
            {
                "session_id": s_id,
                "role": "system",
                "content": "System initialized in offline mode.",
                "intent": "system_init",
                "action_taken": "configure",
                "provider": "system",
                "model": "kernel-v1",
                "latency_ms": 0,
                "timestamp": "2026-11-01T10:00:00.000000",
            },
            {
                "session_id": s_id,
                "role": "user",
                "content": "Check CPU temperature and fan speed",
                "intent": "system_diagnostic",
                "action_taken": None,
                "provider": None,
                "model": None,
                "latency_ms": None,
                "timestamp": "2026-11-01T10:00:01.123456",
            },
            {
                "session_id": s_id,
                "role": "assistant",
                "content": "CPU is 48°C and fan is operating at 2100 RPM.",
                "intent": "system_diagnostic",
                "action_taken": "execute",
                "provider": "sysfs-reader",
                "model": "hardware-monitor",
                "latency_ms": 38,
                "timestamp": "2026-11-01T10:00:01.456789",
            },
        ]

        for turn in test_turns:
            await active_db.save_message(turn)

        retrieved = await active_db.get_recent_messages(limit=10, session_id=s_id)
        assert len(retrieved) == 3

        for orig, ret in zip(test_turns, retrieved):
            assert ret["session_id"] == orig["session_id"]
            assert ret["role"] == orig["role"]
            assert ret["content"] == orig["content"]
            assert ret["intent"] == orig["intent"]
            assert ret["action_taken"] == orig["action_taken"]
            assert ret["provider"] == orig["provider"]
            assert ret["model"] == orig["model"]
            assert ret["latency_ms"] == orig["latency_ms"]
            assert ret["timestamp"] == orig["timestamp"]

    @pytest.mark.asyncio
    async def test_chronological_ordering_under_subsecond_timestamps(self, active_db):
        """
        Rapid sequence of 25 messages with sub-millisecond timestamps must remain
        strictly ordered chronologically when retrieved.
        """
        s_id = await active_db.create_session(summary="Microsecond Timestamp Test")

        base_time = "2026-11-01T14:00:00"
        for i in range(25):
            ts = f"{base_time}.{i:06d}"
            await active_db.save_message({
                "session_id": s_id,
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Turn Sequence #{i:02d}",
                "timestamp": ts,
            })

        retrieved = await active_db.get_recent_messages(limit=30, session_id=s_id)
        assert len(retrieved) == 25

        contents = [m["content"] for m in retrieved]
        expected = [f"Turn Sequence #{i:02d}" for i in range(25)]
        assert contents == expected, "Messages must be ordered strictly monotonically by timestamp"

        # Test limit clause: limit=10 should return the 10 MOST RECENT messages, chronologically
        recent_10 = await active_db.get_recent_messages(limit=10, session_id=s_id)
        assert len(recent_10) == 10
        expected_recent_10 = [f"Turn Sequence #{i:02d}" for i in range(15, 25)]
        assert [m["content"] for m in recent_10] == expected_recent_10

    @pytest.mark.asyncio
    async def test_session_lifecycle_and_end_session(self, active_db):
        """
        Verify session creation, updating ended_at, summary updates, and persistence of
        messages in an ended session.
        """
        s_id = await active_db.create_session(summary="Active Lifecycle Session")
        sess = await active_db.get_session(s_id)
        assert sess is not None
        assert sess["id"] == s_id
        assert sess["summary"] == "Active Lifecycle Session"
        assert sess["ended_at"] is None

        # Add message
        await active_db.save_message({
            "session_id": s_id,
            "role": "user",
            "content": "Lifecycle check",
        })

        # End session
        await active_db.end_session(s_id, summary="Completed lifecycle successfully")
        ended_sess = await active_db.get_session(s_id)
        assert ended_sess["ended_at"] is not None
        assert ended_sess["summary"] == "Completed lifecycle successfully"

        # Verify messages in ended session remain intact
        msgs = await active_db.get_recent_messages(limit=10, session_id=s_id)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Lifecycle check"

        # Writing to an ended session (e.g. background audit or post-session note) does not fail
        await active_db.save_message({
            "session_id": s_id,
            "role": "system",
            "content": "Session audit finalized.",
        })
        msgs_after = await active_db.get_recent_messages(limit=10, session_id=s_id)
        assert len(msgs_after) == 2

    @pytest.mark.asyncio
    async def test_message_id_conflict_resolution_and_update(self, active_db):
        """
        Verify that calling save_message with an existing message id updates the record
        in place without creating duplicates or throwing primary key constraint violations.
        """
        s_id = await active_db.create_session(summary="Id Conflict Session")
        fixed_id = "msg-unique-conflict-test-001"

        await active_db.save_message({
            "id": fixed_id,
            "session_id": s_id,
            "role": "assistant",
            "content": "Preliminary draft response...",
            "intent": "draft",
            "latency_ms": 10,
            "timestamp": "2026-11-01T15:00:00",
        })

        # Update the exact same message ID with finalized content
        await active_db.save_message({
            "id": fixed_id,
            "session_id": s_id,
            "role": "assistant",
            "content": "Finalized production response.",
            "intent": "final",
            "latency_ms": 35,
            "timestamp": "2026-11-01T15:00:01",
        })

        msgs = await active_db.get_recent_messages(limit=10, session_id=s_id)
        assert len(msgs) == 1, "Should have exactly 1 record updated in place, no duplicate"
        assert msgs[0]["id"] == fixed_id
        assert msgs[0]["content"] == "Finalized production response."
        assert msgs[0]["intent"] == "final"
        assert msgs[0]["latency_ms"] == 35

    @pytest.mark.asyncio
    async def test_extreme_dialogue_payloads_unicode_and_sql_injection(self, active_db):
        """
        Stress test with extreme inputs:
        - 50,000 character strings
        - Multi-lingual unicode and emojis
        - Malicious SQL injection payloads
        """
        s_id = await active_db.create_session(summary="Extreme Payloads Session")

        # 1. 50,000 character payload
        huge_payload = "A" * 50000
        await active_db.save_message({
            "session_id": s_id,
            "role": "user",
            "content": huge_payload,
        })

        # 2. Complex unicode, markdown, emojis
        unicode_payload = "SereinOS 塞壬 🤖 🧠 | \n\t`code_block` | Arabic: مرحباً | Math: ∀x∈ℝ: x²≥0"
        await active_db.save_message({
            "session_id": s_id,
            "role": "assistant",
            "content": unicode_payload,
        })

        # 3. SQL injection attempts
        sqli_payload = "'; DROP TABLE conversation_messages; DROP TABLE conversation_sessions; SELECT * FROM sqlite_master WHERE '1'='1"
        await active_db.save_message({
            "session_id": s_id,
            "role": "user",
            "content": sqli_payload,
        })

        msgs = await active_db.get_recent_messages(limit=10, session_id=s_id)
        assert len(msgs) == 3
        assert msgs[0]["content"] == huge_payload
        assert len(msgs[0]["content"]) == 50000
        assert msgs[1]["content"] == unicode_payload
        assert msgs[2]["content"] == sqli_payload

        # Ensure conversation_messages and conversation_sessions tables are completely intact
        table_check = await active_db.fetch_one(
            "SELECT count(*) as cnt FROM sqlite_master WHERE type='table' AND name='conversation_messages'"
        )
        assert table_check["cnt"] == 1

    @pytest.mark.asyncio
    async def test_high_concurrency_dialogue_writes(self, active_db):
        """
        Concurrently write 30 messages across 6 tasks simultaneously.
        Verify zero sqlite3.OperationalError: database is locked and zero data corruption.
        """
        session_id = await active_db.create_session(summary="Concurrency Session")

        async def worker_writer(worker_id: int):
            for i in range(5):
                await active_db.save_message({
                    "session_id": session_id,
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": f"Worker {worker_id} message {i}",
                    "timestamp": datetime.now().isoformat(),
                })
                await asyncio.sleep(0.005)

        # Run 6 parallel workers = 30 messages
        tasks = [worker_writer(w) for w in range(6)]
        await asyncio.gather(*tasks)

        all_msgs = await active_db.get_recent_messages(limit=100, session_id=session_id)
        assert len(all_msgs) == 30


# ============================================================================
# 2. User Preferences Persistence Across Database Re-Instantiations
# ============================================================================

class TestUserPreferencesReinstantiationStress:
    """Stress tests verifying user_preferences across multiple Database instances."""

    @pytest.mark.asyncio
    async def test_preference_primitive_and_complex_types_across_reinstantiations(self, temp_db_file):
        """
        Write diverse preference types in Database Instance 1.
        Close Instance 1.
        Open Database Instance 2 on same file path and retrieve all preferences.
        Verify types and values match exactly.
        """
        # Instance 1: Write preferences
        db1 = Database(temp_db_file)
        await db1.connect()

        diverse_prefs = {
            "ui_theme": "cyberpunk_dark",
            "volume_slider": 78,
            "tts_speech_rate": 1.15,
            "offline_enforced": True,
            "camera_enabled": False,
            "avatar_config": {
                "model": "vrm/serein.vrm",
                "viseme_gain": 1.25,
                "eye_blink_interval": [2.5, 4.0],
                "expressions": {"happy": 0.8, "focused": 0.5},
            },
            "recent_projects": ["SereinOS", "PiperTTS", "WhisperGGUF"],
            "empty_dict": {},
            "empty_list": [],
            "unicode_name": "塞壬 · Serein ✨",
        }

        for key, val in diverse_prefs.items():
            await db1.set_preference(key, val)

        await db1.close()
        del db1

        # Instance 2: Read preferences on newly instantiated Database
        db2 = Database(temp_db_file)
        await db2.connect()

        for key, expected_val in diverse_prefs.items():
            actual_val = await db2.get_preference(key)
            assert actual_val == expected_val, f"Key '{key}' mismatch: expected {expected_val!r}, got {actual_val!r}"
            assert type(actual_val) is type(expected_val), f"Key '{key}' type mismatch: expected {type(expected_val)}, got {type(actual_val)}"

        all_prefs = await db2.get_all_preferences()
        assert len(all_prefs) >= len(diverse_prefs)
        for key, expected_val in diverse_prefs.items():
            assert all_prefs[key] == expected_val

        await db2.close()

    @pytest.mark.asyncio
    async def test_preference_updates_and_incremental_persistence(self, temp_db_file):
        """
        Test updating existing keys, adding new keys, and verifying changes persist across
        a 3-generation database instance lifecycle.
        """
        # Gen 1: Initial state
        db1 = Database(temp_db_file)
        await db1.connect()
        await db1.set_preference("brightness", 50)
        await db1.set_preference("wifi_ssid", "VAIO-AP")
        await db1.set_preference("status", "initializing")
        await db1.close()

        # Gen 2: Update and append
        db2 = Database(temp_db_file)
        await db2.connect()
        assert await db2.get_preference("brightness") == 50
        # Update existing
        await db2.set_preference("brightness", 85)
        await db2.set_preference("status", "ready")
        # Add new
        await db2.set_preference("bluetooth_enabled", True)
        await db2.close()

        # Gen 3: Verify Gen 2 mutations survived in brand new instance
        db3 = Database(temp_db_file)
        await db3.connect()
        assert await db3.get_preference("brightness") == 85
        assert await db3.get_preference("wifi_ssid") == "VAIO-AP"
        assert await db3.get_preference("status") == "ready"
        assert await db3.get_preference("bluetooth_enabled") is True
        await db3.close()

    @pytest.mark.asyncio
    async def test_preference_missing_keys_and_defaults(self, active_db):
        """Querying nonexistent keys returns specified default or None."""
        assert await active_db.get_preference("unrecorded_setting") is None
        assert await active_db.get_preference("unrecorded_setting", default=42) == 42
        assert await active_db.get_preference("unrecorded_setting", default={"fallback": True}) == {"fallback": True}

    @pytest.mark.asyncio
    async def test_preference_extreme_payloads_large_json(self, temp_db_file):
        """
        Store large configuration structures (1000 items dictionary) in user_preferences.
        Verify retrieval after database close and re-open.
        """
        db1 = Database(temp_db_file)
        await db1.connect()

        large_dict = {f"pref_key_{i:04d}": {"index": i, "active": i % 2 == 0, "hash": str(uuid4())} for i in range(1000)}
        await db1.set_preference("large_sys_config", large_dict)
        await db1.close()

        db2 = Database(temp_db_file)
        await db2.connect()
        recovered = await db2.get_preference("large_sys_config")
        assert isinstance(recovered, dict)
        assert len(recovered) == 1000
        assert recovered["pref_key_0500"]["index"] == 500
        await db2.close()

    @pytest.mark.asyncio
    async def test_high_concurrency_preference_writes(self, active_db):
        """Concurrently write 20 preferences from 5 parallel tasks."""
        async def pref_writer(worker_id: int):
            for i in range(4):
                k = f"worker_{worker_id}_param_{i}"
                await active_db.set_preference(k, {"val": worker_id * 100 + i})
                await asyncio.sleep(0.005)

        tasks = [pref_writer(w) for w in range(5)]
        await asyncio.gather(*tasks)

        all_prefs = await active_db.get_all_preferences()
        assert len([k for k in all_prefs if k.startswith("worker_")]) == 20


# ============================================================================
# 3. Voice Event Bus Integration (CoreEvents.VOICE_INPUT -> process_input -> VOICE_OUTPUT)
# ============================================================================

class TestVoiceEventBusIntegrationStress:
    """Stress tests for SereinCore voice event bus subscription and dispatch."""

    @pytest.mark.asyncio
    async def test_voice_input_roundtrip_utterance_event_data(self, running_core):
        """
        Publish UtteranceEventData to CoreEvents.VOICE_INPUT.
        Verify SereinCore.process_input executes and publishes CoreEvents.VOICE_OUTPUT.
        Verify both user turn and assistant response are persisted to SQLite.
        """
        output_event_received = asyncio.Event()
        received_outputs = []

        async def voice_out_handler(evt: Event):
            received_outputs.append(evt.data)
            output_event_received.set()

        unsub = event_bus.subscribe(CoreEvents.VOICE_OUTPUT, voice_out_handler)
        try:
            # Publish UtteranceEventData
            utt = UtteranceEventData(
                text="Hello Serein, how is the system running today?",
                confidence=0.97,
                audio_duration=1.4
            )
            await event_bus.publish(Event(type=CoreEvents.VOICE_INPUT, data=utt))

            # Wait for VOICE_OUTPUT
            await asyncio.wait_for(output_event_received.wait(), timeout=5.0)

            assert len(received_outputs) >= 1
            out = received_outputs[0]
            assert isinstance(out, dict)
            assert "text" in out
            assert len(out["text"]) > 0
            assert "session_id" in out

            # Verify SQLite persistence
            sess_id = out["session_id"]
            messages = await running_core.db.get_recent_messages(limit=10, session_id=sess_id)
            assert len(messages) >= 2

            user_turns = [m for m in messages if m["role"] == "user"]
            asst_turns = [m for m in messages if m["role"] == "assistant"]
            assert len(user_turns) >= 1
            assert len(asst_turns) >= 1
            assert user_turns[-1]["content"] == "Hello Serein, how is the system running today?"
            assert asst_turns[-1]["content"] == out["text"]
        finally:
            unsub()

    @pytest.mark.asyncio
    async def test_voice_input_various_utterance_formats(self, running_core):
        """
        SereinCore on_voice_input handles diverse input types gracefully:
        1. UtteranceEventData
        2. Dictionary: {"text": ...}
        3. Simple object with .text attribute
        4. Plain string
        """
        class MockUtteranceObj:
            def __init__(self, t):
                self.text = t

        formats = [
            UtteranceEventData(text="what is your name?", confidence=0.95, audio_duration=0.8),
            {"text": "open terminal", "confidence": 0.90},
            MockUtteranceObj("check memory usage"),
            "remind me to inspect battery in 10 minutes",
        ]

        for payload in formats:
            out_event = asyncio.Event()
            captured = []

            async def handler(evt: Event):
                captured.append(evt.data)
                out_event.set()

            unsub = event_bus.subscribe(CoreEvents.VOICE_OUTPUT, handler)
            try:
                await event_bus.publish(Event(type=CoreEvents.VOICE_INPUT, data=payload))
                await asyncio.wait_for(out_event.wait(), timeout=5.0)
                assert len(captured) == 1
                assert len(captured[0]["text"]) > 0
            finally:
                unsub()

    @pytest.mark.asyncio
    async def test_voice_input_blank_and_whitespace_suppression(self, running_core):
        """Empty, whitespace-only, or None text must NOT trigger process_input or emit VOICE_OUTPUT."""
        output_unexpected = asyncio.Event()

        async def unexpected_handler(evt: Event):
            output_unexpected.set()

        unsub = event_bus.subscribe(CoreEvents.VOICE_OUTPUT, unexpected_handler)
        try:
            # 1. Empty string
            await event_bus.publish(Event(type=CoreEvents.VOICE_INPUT, data=""))
            # 2. Whitespace string
            await event_bus.publish(Event(type=CoreEvents.VOICE_INPUT, data="   \t\n   "))
            # 3. UtteranceEventData with empty text
            await event_bus.publish(Event(type=CoreEvents.VOICE_INPUT, data=UtteranceEventData(text="", confidence=0.0)))
            # 4. None data
            await event_bus.publish(Event(type=CoreEvents.VOICE_INPUT, data=None))

            # Small sleep to ensure event loop had cycles to process
            await asyncio.sleep(0.15)
            assert not output_unexpected.is_set(), "Blank/whitespace inputs must be suppressed"
        finally:
            unsub()

    @pytest.mark.asyncio
    async def test_voice_multiturn_session_continuity(self, running_core):
        """
        Multiple sequential voice utterances must persist in the SAME session_id,
        maintaining dialogue continuity.
        """
        utterances = [
            "Good morning Serein",
            "What can you help me with?",
            "open terminal",
        ]

        responses = []
        for utt_text in utterances:
            turn_done = asyncio.Event()
            last_resp = {}

            async def out_handler(evt: Event):
                last_resp.update(evt.data)
                turn_done.set()

            unsub = event_bus.subscribe(CoreEvents.VOICE_OUTPUT, out_handler)
            try:
                await event_bus.publish(Event(
                    type=CoreEvents.VOICE_INPUT,
                    data=UtteranceEventData(text=utt_text, confidence=0.99, audio_duration=1.0)
                ))
                await asyncio.wait_for(turn_done.wait(), timeout=5.0)
                responses.append(dict(last_resp))
            finally:
                unsub()

        assert len(responses) == 3
        # All 3 turns must share the exact same session_id
        session_ids = [r["session_id"] for r in responses]
        assert len(set(session_ids)) == 1, f"All turns must share one session_id: {session_ids}"

        # Check dialogue history in SQLite has all 6 messages (3 user + 3 assistant)
        common_sess_id = session_ids[0]
        history = await running_core.db.get_recent_messages(limit=10, session_id=common_sess_id)
        assert len(history) == 6
        assert [m["content"] for m in history if m["role"] == "user"] == utterances

    @pytest.mark.asyncio
    async def test_voice_rapid_burst_stress(self, running_core):
        """
        Rapid burst of 5 voice inputs dispatched simultaneously or in sub-millisecond intervals.
        Verify SereinCore processes all inputs without SQLite lock deadlocks or lost messages.
        """
        burst_queries = [
            "What is SereinOS?",
            "Show system status",
            "Who are you?",
            "What time is it?",
            "open terminal",
        ]

        received_outputs = []
        all_done = asyncio.Event()

        async def burst_handler(evt: Event):
            received_outputs.append(evt.data)
            if len(received_outputs) == len(burst_queries):
                all_done.set()

        unsub = event_bus.subscribe(CoreEvents.VOICE_OUTPUT, burst_handler)
        try:
            # Publish 5 events in rapid succession
            for q in burst_queries:
                await event_bus.publish(Event(
                    type=CoreEvents.VOICE_INPUT,
                    data=UtteranceEventData(text=q, confidence=0.95, audio_duration=0.5)
                ))

            await asyncio.wait_for(all_done.wait(), timeout=10.0)
            assert len(received_outputs) == len(burst_queries)

            # Check that all received outputs have non-empty text
            for out in received_outputs:
                assert len(out.get("text", "")) > 0

            # Verify SQLite recorded all 5 turns (10 messages)
            sess_id = received_outputs[0]["session_id"]
            msgs = await running_core.db.get_recent_messages(limit=20, session_id=sess_id)
            user_texts = [m["content"] for m in msgs if m["role"] == "user"]
            for q in burst_queries:
                assert q in user_texts
        finally:
            unsub()

    @pytest.mark.asyncio
    async def test_voice_shutdown_cleanup_and_no_dangling_handlers(self, temp_db_file):
        """
        Verify that shutting down SereinCore unsubscribes all voice event handlers,
        and subsequent events do not trigger stale callbacks or cause errors.
        """
        cfg = CoreConfig()
        cfg.database.path = temp_db_file
        core = SereinCore(cfg)
        await core.initialize()

        # Shutdown core
        await core.shutdown()

        # Publish VOICE_INPUT after shutdown
        output_received = asyncio.Event()

        async def handler(evt: Event):
            output_received.set()

        unsub = event_bus.subscribe(CoreEvents.VOICE_OUTPUT, handler)
        try:
            await event_bus.publish(Event(
                type=CoreEvents.VOICE_INPUT,
                data=UtteranceEventData(text="Should not be answered", confidence=0.99)
            ))
            await asyncio.sleep(0.15)
            assert not output_received.is_set(), "Post-shutdown events must not be processed"
        finally:
            unsub()
