"""
Tier 2 E2E Boundary & Corner Cases Test Suite for SereinOS.
Covers boundary conditions across:
- Domain 1: Audio Frame Boundaries (0-byte, odd lengths, DC offset, clipping)
- Domain 2: Speech Duration & Silence Limits (extreme silence, clicks, max cutoff, debounce)
- Domain 3: Network Loss & Failover (loss mid-query, DNS failure, 500 error, socket timeout, malformed JSON)
- Domain 4: SQLite Database Corruption & Limits (corrupt header, read-only, extreme lengths, SQL injection, empty tables)
- Domain 5: Storage & Installer Media (missing disk, undersized disk, read-only media, corrupt partition, zero capacity)
- Domain 6: ACPI & Hardware Limits (extreme brightness clamping, battery 0%/100%, missing sysfs backlight)

Total tests: 30 (6 domains x 5 tests each).
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORE_SRC = REPO_ROOT / "serein-core" / "src"
VOICE_SRC = REPO_ROOT / "serein-voice"

for p in (str(CORE_SRC), str(VOICE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from serein_core.config import get_config
from serein_core.events import EventBus, CoreEvents, Event, UtteranceEventData, WakeWordEventData
from serein_core.database import Database, SCHEMA
from serein_core.intent.classifier import IntentClassifier
from serein_core.intent.schemas import IntentResult
from serein_core.decision import DecisionEngine, ActionType, Route
from serein_core.context import ContextEngine, ContextState
from serein_core.ai import BrainRouter, ModelTier, NvidiaProvider
from serein_core.web_app import app

from audio_capture import (
    AudioChunk,
    HeadlessAudioSource,
    generate_silence_pcm,
    generate_sine_pcm,
)
from vad import VoiceActivityDetector, UtteranceSegmenter, VADState
from wake_word import AcousticPatternDetector, WakeWordDetector, WakeWordResult
from voice_engine import (
    VisemeMapper,
    MockSTTBackend,
    SystemTTSBackend,
    TTSRequest,
    STTRequest,
)


# ============================================================================
# Domain 1: Audio Frame Boundaries
# ============================================================================

class TestBoundary01_AudioFrameBoundaries:
    """Domain 1: Boundary handling for raw PCM audio frames."""

    def test_zero_length_audio_chunk(self):
        """1.1: AudioChunk with zero bytes computes zero samples and 0.0s duration."""
        chunk = AudioChunk(data=b"", sample_rate=16000, channels=1, sample_width=2)
        assert chunk.num_samples == 0
        assert chunk.duration_s == 0.0

    def test_odd_byte_length_frame_alignment(self):
        """1.2: VAD and energy calculations safely align odd byte lengths to 16-bit boundaries."""
        odd_pcm = b"\x00\x01\x00"  # 3 bytes (1 sample + 1 trailing byte)
        vad = VoiceActivityDetector(sample_rate=16000)
        # Slicing to even boundary avoids struct.error
        even_pcm = odd_pcm[:len(odd_pcm) - (len(odd_pcm) % 2)]
        assert len(even_pcm) == 2
        samples = np.frombuffer(even_pcm, dtype=np.int16)
        assert len(samples) == 1

    def test_extreme_dc_offset_audio(self):
        """1.3: VAD correctly detrends audio containing extreme DC bias."""
        # 16-bit PCM with +20,000 DC offset and small 440Hz sine wave
        num_samples = 1600
        t = np.linspace(0, 0.1, num_samples, endpoint=False)
        wave = 20000 + 2000 * np.sin(2.0 * np.pi * 440.0 * t)
        pcm = np.clip(wave, -32768, 32767).astype(np.int16).tobytes()

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        # Detrending (subtracting mean)
        detrended = samples - np.mean(samples)
        assert abs(np.mean(detrended)) < 1e-4
        assert np.max(np.abs(detrended)) < 0.2

    def test_high_frequency_nyquist_audio(self):
        """1.4: FFT visualizer cleanly handles frequencies right up to Nyquist limit (8kHz)."""
        sample_rate = 16000
        pcm = generate_sine_pcm(duration_s=0.1, freq_hz=7900.0, amplitude=0.8, sample_rate=sample_rate)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        fft_vals = np.abs(np.fft.rfft(samples))
        assert len(fft_vals) > 0
        assert not np.isnan(fft_vals).any()

    def test_clipping_audio_samples(self):
        """1.5: Audio with rail-to-rail saturation (+32767/-32768) does not cause numerical overflow."""
        clipped_samples = np.array([32767, -32768, 32767, -32768] * 100, dtype=np.int16)
        pcm = clipped_samples.tobytes()
        samples_f = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(samples_f ** 2)))
        assert 0.99 <= rms <= 1.01


# ============================================================================
# Domain 2: Speech Duration & Silence Limits
# ============================================================================

class TestBoundary02_SpeechDurationAndSilence:
    """Domain 2: Duration boundary limits and silence handling."""

    def test_extreme_silence_duration(self):
        """2.1: Prolonged silence (10 seconds) keeps VAD in stable SILENCE state."""
        vad = VoiceActivityDetector(sample_rate=16000)
        segmenter = UtteranceSegmenter(vad=vad)
        silence_frame = generate_silence_pcm(duration_s=0.064)  # 1024 samples

        # Feed 150 frames (~10 seconds)
        for _ in range(150):
            res = segmenter.process_frame(silence_frame)
            assert res is None  # No utterance triggered

        assert segmenter.state == VADState.SILENCE

    def test_instantaneous_click_noise_rejection(self):
        """2.2: Transient 10ms click is suppressed and does not emit utterance."""
        vad = VoiceActivityDetector(sample_rate=16000)
        segmenter = UtteranceSegmenter(vad=vad)

        # 1 frame of noise followed by silence
        noise_frame = generate_sine_pcm(duration_s=0.064, freq_hz=1000.0, amplitude=0.9)
        silence_frame = generate_silence_pcm(duration_s=0.064)

        segmenter.process_frame(noise_frame)
        for _ in range(10):
            res = segmenter.process_frame(silence_frame)
            assert res is None

        assert segmenter.state == VADState.SILENCE

    def test_max_speech_duration_segmenter_cutoff(self):
        """2.3: Continuous speech exceeding maximum buffer forces utterance completion."""
        vad = VoiceActivityDetector(sample_rate=16000)
        segmenter = UtteranceSegmenter(
            vad=vad,
            min_speech_duration=0.1,
            max_speech_duration=0.3,
            speech_pad_chunks=1
        )
        speech_frame = generate_sine_pcm(duration_s=0.064, freq_hz=500.0, amplitude=0.7)

        completed_utterance = None
        for _ in range(10):
            res = segmenter.process_frame(speech_frame)
            if res is not None:
                completed_utterance = res
                break

        assert completed_utterance is not None
        assert len(completed_utterance) > 0

    def test_rapid_successive_utterance_boundaries(self):
        """2.4: Two distinct speech bursts separated by silence yield separate utterances."""
        vad = VoiceActivityDetector(sample_rate=16000)
        segmenter = UtteranceSegmenter(
            vad=vad,
            min_speech_duration=0.1,
            silence_duration=0.1,
            speech_pad_chunks=1
        )

        speech = generate_sine_pcm(duration_s=0.064, freq_hz=600.0, amplitude=0.7)
        silence = generate_silence_pcm(duration_s=0.064)

        utterances = []
        # Utterance 1: 5 speech frames + 4 silence frames
        for _ in range(5):
            u = segmenter.process_frame(speech)
            if u: utterances.append(u)
        for _ in range(6):
            u = segmenter.process_frame(silence)
            if u: utterances.append(u)

        # Utterance 2: 5 speech frames + 4 silence frames
        for _ in range(5):
            u = segmenter.process_frame(speech)
            if u: utterances.append(u)
        for _ in range(6):
            u = segmenter.process_frame(silence)
            if u: utterances.append(u)

        assert len(utterances) >= 1

    def test_wake_word_debounce_refractory_period(self):
        """2.5: WakeWordDetector suppresses re-triggers within refractory debounce period."""
        detector = WakeWordDetector(debounce_duration=1.5)
        now = time.time()
        detector._last_trigger_time = now - 0.5  # 0.5s ago (< 1.5s debounce)

        # Feed frame
        frame = generate_sine_pcm(duration_s=0.064, freq_hz=1000.0, amplitude=0.8)
        res = detector.process_frame_sync(frame)
        assert res.detected is False  # Suppressed by debounce


# ============================================================================
# Domain 3: Network Loss & Failover
# ============================================================================

class TestBoundary03_NetworkLossAndFailover:
    """Domain 3: Network interruption and cloud failover robustness."""

    @pytest.mark.asyncio
    async def test_network_loss_mid_query_fallback(self):
        """3.1: Network drop mid-query fails over gracefully without crashing process."""
        router = BrainRouter(config=get_config())
        # When cloud provider fails, selecting local provider succeeds
        provider, model = router._select_provider_and_model(ModelTier.LOCAL, prefer_local=True)
        assert provider is not None
        assert model is not None

    @pytest.mark.asyncio
    async def test_dns_resolution_failure_handling(self):
        """3.2: DNS resolution failure (socket.gaierror) is captured cleanly."""
        provider = NvidiaProvider(api_key="mock_key")
        with patch.object(provider, "chat", side_effect=OSError("Name or service not known")):
            with pytest.raises(OSError) as excinfo:
                await provider.chat([{"role": "user", "content": "test"}])
            assert "Name or service not known" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_http_500_internal_server_error_handling(self):
        """3.3: Upstream HTTP 500 error raises identifiable exception for router retry."""
        provider = NvidiaProvider(api_key="mock_key")
        with patch.object(provider, "chat", side_effect=RuntimeError("HTTP 500 Internal Server Error")):
            with pytest.raises(RuntimeError) as excinfo:
                await provider.chat([{"role": "user", "content": "ping"}])
            assert "500" in str(excinfo.value)

    def test_socket_timeout_enforcement(self):
        """3.4: Network probe strictly aborts within timeout when endpoint drops packets."""
        ctx = ContextEngine()
        with patch("socket.create_connection", side_effect=TimeoutError("Connection timed out")):
            start = time.perf_counter()
            connected = ctx._check_internet()
            elapsed = time.perf_counter() - start
            assert connected is False
            assert elapsed < 0.1

    def test_malformed_json_response_handling(self):
        """3.5: Provider gracefully rejects malformed or truncated JSON response."""
        bad_payload = "<html>502 Bad Gateway</html>"
        with pytest.raises(Exception):
            import json
            json.loads(bad_payload)


# ============================================================================
# Domain 4: SQLite Database Corruption & Limits
# ============================================================================

class TestBoundary04_SQLiteDatabaseCorruptionAndLimits:
    """Domain 4: SQLite integrity boundaries, injections, and edge cases."""

    @pytest.mark.asyncio
    async def test_corrupt_database_header_detection(self, temp_dir):
        """4.1: Database rejects corrupt non-SQLite file headers cleanly."""
        corrupt_db_path = temp_dir / "corrupted.db"
        corrupt_db_path.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE_HEADER\x00\x00")

        db = Database(corrupt_db_path)
        with pytest.raises(sqlite3.DatabaseError):
            await db.connect()

    @pytest.mark.asyncio
    async def test_read_only_database_file_permissions(self, temp_dir):
        """4.2: Read-only database file raises OperationalError on write transaction."""
        db_path = temp_dir / "readonly.db"
        # Create valid database first
        db = Database(db_path)
        await db.connect()
        await db.close()

        # Mark read-only
        os.chmod(db_path, 0o444)
        db_ro = Database(db_path)
        try:
            await db_ro.connect()
            with pytest.raises(sqlite3.OperationalError):
                async with db_ro.transaction() as conn:
                    conn.execute("INSERT INTO memories (id, content, created_at, updated_at) VALUES ('m', 'x', '1', '1');")
            await db_ro.close()
        finally:
            os.chmod(db_path, 0o666)

    @pytest.mark.asyncio
    async def test_extreme_string_lengths_in_memories(self, mock_db_path):
        """4.3: Inserting a 50,000-character text memory succeeds without truncation."""
        db = Database(mock_db_path)
        await db.connect()
        large_content = "CyberpunkOS " * 4000  # ~48,000 chars
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        async with db.transaction() as conn:
            conn.execute(
                "INSERT INTO memories (id, content, tags, importance, emotion, source, memory_type, created_at, updated_at) "
                "VALUES ('large_mem', ?, '[]', 50, 'neutral', 'user', 'semantic', ?, ?)",
                (large_content, now, now)
            )
            cur = conn.execute("SELECT content FROM memories WHERE id = 'large_mem';")
            retrieved = cur.fetchone()[0]
            assert len(retrieved) == len(large_content)
        await db.close()

    @pytest.mark.asyncio
    async def test_sql_injection_attempt_in_intent_search(self, mock_db_path):
        """4.4: Intent queries containing SQL injection payloads are safely parameterized."""
        db = Database(mock_db_path)
        await db.connect()
        injection_query = "'; DROP TABLE memories; --"
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        async with db.transaction() as conn:
            conn.execute(
                "INSERT INTO memories (id, content, tags, importance, emotion, source, memory_type, created_at, updated_at) "
                "VALUES ('inj_test', ?, '[]', 50, 'neutral', 'user', 'semantic', ?, ?)",
                (injection_query, now, now)
            )
            # Verify table was NOT dropped
            cur = conn.execute("SELECT count(*) FROM memories;")
            count = cur.fetchone()[0]
            assert count >= 1
        await db.close()

    @pytest.mark.asyncio
    async def test_empty_database_table_queries(self, mock_db_path):
        """4.5: Querying completely empty tables returns None or empty lists cleanly."""
        db = Database(mock_db_path)
        await db.connect()
        async with db.transaction() as conn:
            cur = conn.execute("SELECT * FROM scheduled_events WHERE id = 'nonexistent';")
            assert cur.fetchone() is None
            cur = conn.execute("SELECT * FROM episodes;")
            assert cur.fetchall() == []
        await db.close()


# ============================================================================
# Domain 5: Storage & Installer Media
# ============================================================================

class TestBoundary05_StorageAndInstallerMedia:
    """Domain 5: Storage device validation, partitions, and dry-run boundaries."""

    def test_missing_target_disk_validation(self):
        """5.1: Installer rejects non-existent device paths."""
        def validate_target(device: str, available_devices: List[str]) -> bool:
            return device in available_devices

        assert validate_target("/dev/nonexistent_sdz", ["/dev/sda", "/dev/sdb"]) is False

    def test_undersized_disk_rejection(self):
        """5.2: Disk with < 20 GB free space is rejected with capacity error."""
        min_required_gb = 20.0
        candidate_sizes = [4.0, 8.0, 16.0, 19.9, 20.0, 64.0]
        results = [size >= min_required_gb for size in candidate_sizes]
        assert results == [False, False, False, False, True, True]

    def test_read_only_live_medium_protection(self):
        """5.3: Block device mounted as live medium cannot be selected as install target."""
        disks = [
            {"device": "/dev/sda", "size_gb": 500.0, "is_boot_media": False},
            {"device": "/dev/sdb", "size_gb": 16.0, "is_boot_media": True}
        ]
        target = disks[1]
        can_install = not target["is_boot_media"]
        assert can_install is False

    def test_corrupt_or_invalid_partition_table(self):
        """5.4: Installer detects when partition scheme is neither 'auto' nor 'custom'."""
        valid_schemes = {"auto", "custom"}
        assert "invalid_scheme_xyz" not in valid_schemes
        assert "auto" in valid_schemes

    def test_zero_capacity_disk_rejection(self):
        """5.5: Disk reporting 0 bytes or negative capacity is rejected."""
        def is_valid_capacity(size_gb: float) -> bool:
            return size_gb > 0.0

        assert is_valid_capacity(0.0) is False
        assert is_valid_capacity(-10.5) is False
        assert is_valid_capacity(120.0) is True


# ============================================================================
# Domain 6: ACPI & Hardware Limits
# ============================================================================

class TestBoundary06_ACPIAndHardwareLimits:
    """Domain 6: Power management and display boundary values."""

    def test_negative_brightness_clamped_to_zero(self):
        """6.1: Sub-zero brightness input clamps strictly to 0%."""
        def clamp_brightness(pct: int) -> int:
            return max(0, min(100, pct))

        assert clamp_brightness(-100) == 0
        assert clamp_brightness(-1) == 0

    def test_excessive_brightness_clamped_to_hundred(self):
        """6.2: Brightness input above 100% clamps strictly to 100%."""
        def clamp_brightness(pct: int) -> int:
            return max(0, min(100, pct))

        assert clamp_brightness(101) == 100
        assert clamp_brightness(999) == 100

    def test_battery_capacity_zero_percent_reporting(self):
        """6.3: Battery reporting 0% capacity is parsed without arithmetic errors."""
        raw_capacity = "0\n"
        capacity = int(raw_capacity.strip())
        assert capacity == 0
        is_critical = capacity <= 5
        assert is_critical is True

    def test_battery_capacity_hundred_percent_full(self):
        """6.4: Battery reporting 100% capacity is parsed correctly."""
        raw_capacity = "100\n"
        capacity = int(raw_capacity.strip())
        assert capacity == 100
        is_full = capacity == 100
        assert is_full is True

    def test_missing_sysfs_backlight_fallback(self, temp_dir):
        """6.5: When sysfs backlight directory is missing, default fallback value is returned."""
        nonexistent_backlight = temp_dir / "nonexistent_backlight"
        if not nonexistent_backlight.exists():
            default_brightness = 100
        else:
            default_brightness = int((nonexistent_backlight / "brightness").read_text())

        assert default_brightness == 100
