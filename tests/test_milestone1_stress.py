"""
Milestone 1 Empirical Stress Tests & Boundary Verification
Executed by Challenger 1.
"""
import sys
import os
import json
import time
import math
from dataclasses import asdict
from pathlib import Path
import pytest
from pydantic import ValidationError

# Ensure both serein-core and serein-voice are importable
CORE_SRC = Path(__file__).resolve().parent.parent / "serein-core" / "src"
VOICE_SRC = Path(__file__).resolve().parent.parent / "serein-voice"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))
if str(VOICE_SRC) not in sys.path:
    sys.path.insert(0, str(VOICE_SRC))

import serein_core.config as core_config_module
from serein_core.config import VoiceConfig, CoreConfig, get_config
from serein_core.events import (
    Event,
    EventBus,
    CoreEvents,
    WakeWordEventData,
    UtteranceEventData,
    event_bus,
)
from voice_engine import VisemeMapper


# ============================================================================
# 1. VoiceConfig Stress Tests & Boundary Verification
# ============================================================================

class TestVoiceConfigBoundaries:
    """Stress tests and boundary checks on VoiceConfig."""

    def test_default_values(self):
        """Verify default values meet Milestone 1 specification."""
        cfg = VoiceConfig()
        assert cfg.enabled is True
        assert cfg.vad_energy_threshold == 0.015
        assert cfg.vad_silence_duration == 1.5
        assert cfg.vad_preroll_duration == 0.3
        assert cfg.vad_min_speech_duration == 0.4
        assert cfg.vad_max_speech_duration == 15.0
        assert cfg.wake_phrase == "Hey Serein"
        assert cfg.wake_sensitivity == 0.5
        assert cfg.wake_debounce_duration == 1.5
        assert cfg.capture_mode == "headless"
        assert cfg.mock_wake_word is True
        assert cfg.mock_stt is True

    def test_custom_valid_values(self):
        """Verify custom valid configuration values."""
        cfg = VoiceConfig(
            vad_energy_threshold=0.03,
            vad_silence_duration=2.0,
            vad_preroll_duration=0.5,
            vad_min_speech_duration=0.5,
            vad_max_speech_duration=30.0,
            wake_phrase="Computer",
            wake_sensitivity=0.8,
            wake_debounce_duration=3.0,
            capture_mode="hardware",
            mock_wake_word=False,
            mock_stt=False,
        )
        assert cfg.vad_energy_threshold == 0.03
        assert cfg.wake_phrase == "Computer"
        assert cfg.capture_mode == "hardware"
        assert cfg.mock_wake_word is False

    def test_boundary_zero_and_extreme_values(self):
        """Test zero and large extreme boundary numbers."""
        cfg = VoiceConfig(
            vad_energy_threshold=0.0,
            vad_silence_duration=0.0,
            vad_preroll_duration=0.0,
            vad_min_speech_duration=0.0,
            vad_max_speech_duration=3600.0,
            wake_sensitivity=1.0,
            wake_debounce_duration=0.0,
        )
        assert cfg.vad_energy_threshold == 0.0
        assert cfg.vad_silence_duration == 0.0
        assert cfg.wake_sensitivity == 1.0

    def test_negative_values_allowed_or_prevented(self):
        """Check whether negative values are accepted (evaluating lack of validation constraints)."""
        # Pydantic Fields in VoiceConfig currently do NOT declare ge=0
        cfg = VoiceConfig(
            vad_energy_threshold=-0.5,
            vad_silence_duration=-1.0,
            vad_min_speech_duration=-2.0,
            wake_sensitivity=-1.0,
        )
        # Note: If no validators are defined, these are stored as negative numbers.
        assert cfg.vad_energy_threshold == -0.5
        assert cfg.wake_sensitivity == -1.0

    def test_inverted_min_max_durations(self):
        """Test when min speech duration exceeds max speech duration."""
        cfg = VoiceConfig(
            vad_min_speech_duration=20.0,
            vad_max_speech_duration=5.0,
        )
        # No cross-field validator currently checks min <= max
        assert cfg.vad_min_speech_duration > cfg.vad_max_speech_duration

    def test_type_coercion_and_invalid_types(self):
        """Test type coercion (e.g. numeric strings) and rejection of invalid types."""
        # Pydantic coerces numeric strings
        cfg = VoiceConfig(
            vad_energy_threshold="0.025",
            vad_silence_duration="2.5",
            mock_wake_word="true",
        )
        assert cfg.vad_energy_threshold == 0.025
        assert cfg.vad_silence_duration == 2.5
        assert cfg.mock_wake_word is True

        # Non-numeric string into float should raise ValidationError
        with pytest.raises(ValidationError):
            VoiceConfig(vad_energy_threshold="invalid_number")

        with pytest.raises(ValidationError):
            VoiceConfig(wake_sensitivity="not_a_float")

        # None into non-optional string or float
        with pytest.raises(ValidationError):
            VoiceConfig(wake_phrase=None)

        with pytest.raises(ValidationError):
            VoiceConfig(capture_mode=None)

    def test_core_config_yaml_serialization_roundtrip(self, tmp_path):
        """Test CoreConfig YAML export and reload preserving VoiceConfig."""
        yaml_file = tmp_path / "test_config.yaml"
        core = CoreConfig(
            voice=VoiceConfig(
                vad_energy_threshold=0.042,
                wake_phrase="Hey Assistant",
                capture_mode="hardware",
                mock_wake_word=False,
            )
        )
        core.to_yaml(yaml_file)
        assert yaml_file.exists()

        reloaded = CoreConfig.from_yaml(yaml_file)
        assert reloaded.voice.vad_energy_threshold == 0.042
        assert reloaded.voice.wake_phrase == "Hey Assistant"
        assert reloaded.voice.capture_mode == "hardware"
        assert reloaded.voice.mock_wake_word is False

    def test_backward_compat_config_getattr(self):
        """Test backward compatibility module-level __getattr__ on serein_core.config."""
        import serein_core.config as cfg_mod
        direct_config = cfg_mod.config
        assert direct_config is not None
        assert hasattr(direct_config, "voice")
        assert direct_config.voice.wake_phrase == "Hey Serein"

        with pytest.raises(AttributeError):
            _ = cfg_mod.non_existent_attribute_xyz


# ============================================================================
# 2. Dataclass Serialization Stress Tests
# ============================================================================

class TestEventDataSerialization:
    """Stress tests and serialization checks for WakeWordEventData and UtteranceEventData."""

    def test_wakeword_event_data_defaults(self):
        """Verify defaults for WakeWordEventData."""
        before = time.time()
        ww = WakeWordEventData()
        after = time.time()
        assert ww.wake_word == "Hey Serein"
        assert ww.confidence == 1.0
        assert before <= ww.timestamp <= after
        assert ww.source == "voice_engine"
        assert ww.metadata == {}

    def test_utterance_event_data_defaults(self):
        """Verify defaults for UtteranceEventData."""
        before = time.time()
        utt = UtteranceEventData()
        after = time.time()
        assert utt.text == ""
        assert utt.confidence == 1.0
        assert utt.audio_duration == 0.0
        assert utt.language == "en"
        assert utt.is_final is True
        assert before <= utt.timestamp <= after
        assert utt.metadata == {}

    def test_metadata_mutable_default_isolation(self):
        """Verify instances do not share mutable default metadata dict."""
        ww1 = WakeWordEventData()
        ww2 = WakeWordEventData()
        ww1.metadata["key"] = "value"
        assert "key" not in ww2.metadata

        utt1 = UtteranceEventData()
        utt2 = UtteranceEventData()
        utt1.metadata["tag"] = 999
        assert "tag" not in utt2.metadata

    def test_asdict_and_json_roundtrip_wakeword(self):
        """Test json round-trip for WakeWordEventData."""
        ww = WakeWordEventData(
            wake_word="Hey Serein",
            confidence=0.975,
            timestamp=1700000000.123,
            source="microphone_stream",
            metadata={"snr": 24.5, "model": "openwakeword", "tags": ["test", "ci"]},
        )
        d = asdict(ww)
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        reconstructed = WakeWordEventData(**loaded)
        assert reconstructed == ww

    def test_asdict_and_json_roundtrip_utterance(self):
        """Test json round-trip for UtteranceEventData."""
        utt = UtteranceEventData(
            text="Open the pod bay doors, please.",
            confidence=0.99,
            audio_duration=3.45,
            language="en-US",
            is_final=True,
            timestamp=1700000005.5,
            metadata={"vad_chunks": 12, "speaker_id": "user_01"},
        )
        d = asdict(utt)
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        reconstructed = UtteranceEventData(**loaded)
        assert reconstructed == utt

    def test_event_wrapping_and_serialization(self):
        """Test Event instance wrapping dataclass payloads."""
        ww = WakeWordEventData(wake_word="Hey Serein", confidence=0.88)
        event = Event(
            type=CoreEvents.WAKE_WORD_DETECTED,
            data=ww,
            source="voice_engine",
        )
        assert event.type == CoreEvents.WAKE_WORD_DETECTED
        assert event.data.wake_word == "Hey Serein"
        assert event.event_id is not None

        utt = UtteranceEventData(text="Hello world", confidence=0.92)
        event_utt = Event(
            type=CoreEvents.VOICE_INPUT,
            data=utt,
            source="voice_engine",
        )
        assert event_utt.type == CoreEvents.VOICE_INPUT
        assert event_utt.data.text == "Hello world"

    def test_extreme_and_boundary_payloads(self):
        """Test empty strings, unicode, and large numbers."""
        utt_unicode = UtteranceEventData(
            text="你好，世界！🌍 12345! @#$%",
            confidence=0.0,
            audio_duration=0.0,
            language="zh-CN",
            is_final=False,
            metadata={"nested": {"a": [1, 2, 3], "b": None}},
        )
        d = asdict(utt_unicode)
        json_str = json.dumps(d, ensure_ascii=False)
        loaded = json.loads(json_str)
        assert loaded["text"] == "你好，世界！🌍 12345! @#$%"
        assert loaded["metadata"]["nested"]["b"] is None

        # Reconstructed
        reconstructed = UtteranceEventData(**loaded)
        assert reconstructed == utt_unicode


# ============================================================================
# 3. VisemeMapper Edge Cases & Stress Tests
# ============================================================================

class TestVisemeMapperEdgeCases:
    """Stress tests and boundary checks for pure-Python VisemeMapper fallback."""

    ALL_15_SHAPES = [
        "aa", "ih", "e", "oh", "ou", "pp", "ff", "th",
        "dd", "kk", "ch", "ss", "nn", "rr", "sil"
    ]

    def test_azure_viseme_valid_range_0_to_21(self):
        """Verify all Azure viseme IDs 0..21 produce all 15 blend shapes."""
        for v_id in range(22):
            weights = VisemeMapper.MapAzureViseme(v_id, intensity=1.0)
            assert isinstance(weights, dict)
            assert set(weights.keys()) == set(self.ALL_15_SHAPES)
            for k, val in weights.items():
                assert isinstance(val, (int, float))
                assert val >= 0.0, f"Viseme {v_id} produced negative weight for {k}"

    def test_azure_viseme_specific_mappings(self):
        """Verify specific expected blend shapes for key Azure visemes."""
        # 0 -> sil
        w0 = VisemeMapper.MapAzureViseme(0)
        assert w0["sil"] == 1.0
        assert sum(v for k, v in w0.items() if k != "sil") == 0.0

        # 10 -> aa
        w10 = VisemeMapper.MapAzureViseme(10)
        assert w10["aa"] == 1.0

        # 15 -> compound: rr: 0.8, aa: 0.3
        w15 = VisemeMapper.MapAzureViseme(15)
        assert w15["rr"] == pytest.approx(0.8)
        assert w15["aa"] == pytest.approx(0.3)

        # 21 -> compound: ch: 0.9, ss: 0.2
        w21 = VisemeMapper.MapAzureViseme(21)
        assert w21["ch"] == pytest.approx(0.9)
        assert w21["ss"] == pytest.approx(0.2)

    def test_azure_viseme_intensity_scaling(self):
        """Test intensity multipliers: 0.0, 0.5, 2.0."""
        w_half = VisemeMapper.MapAzureViseme(10, intensity=0.5)
        assert w_half["aa"] == pytest.approx(0.5)

        w_zero = VisemeMapper.MapAzureViseme(10, intensity=0.0)
        assert all(v == 0.0 for v in w_zero.values())

        w_double = VisemeMapper.MapAzureViseme(15, intensity=2.0)
        assert w_double["rr"] == pytest.approx(1.6)
        assert w_double["aa"] == pytest.approx(0.6)

    def test_azure_viseme_out_of_bounds(self):
        """Test out-of-bounds viseme IDs (-1, 22, 100, 9999)."""
        # Out-of-bounds in Python VisemeMapper falls back to [('sil', 1.0)]
        for oob_id in [-1, -99, 22, 100, 9999]:
            weights = VisemeMapper.MapAzureViseme(oob_id, intensity=1.0)
            assert weights["sil"] == 1.0
            assert sum(v for k, v in weights.items() if k != "sil") == 0.0

    def test_azure_viseme_non_integer_inputs(self):
        """Test non-integer types passed as viseme_id."""
        # String, float, None
        w_str = VisemeMapper.MapAzureViseme("10")
        assert w_str["sil"] == 1.0  # treated as not found -> sil

        w_none = VisemeMapper.MapAzureViseme(None)
        assert w_none["sil"] == 1.0

        w_float = VisemeMapper.MapAzureViseme(10.5)
        assert w_float["sil"] == 1.0

    def test_map_phoneme_valid(self):
        """Test ARPABET phoneme mapping to blend shapes."""
        # Vowels
        assert VisemeMapper.MapPhoneme("AA")["aa"] == 1.0
        assert VisemeMapper.MapPhoneme("AO")["oh"] == 1.0
        assert VisemeMapper.MapPhoneme("UW")["ou"] == 1.0
        assert VisemeMapper.MapPhoneme("IH")["ih"] == 1.0
        assert VisemeMapper.MapPhoneme("EH")["e"] == 1.0

        # Consonants
        assert VisemeMapper.MapPhoneme("P")["pp"] == 1.0
        assert VisemeMapper.MapPhoneme("F")["ff"] == 1.0
        assert VisemeMapper.MapPhoneme("TH")["th"] == 1.0
        assert VisemeMapper.MapPhoneme("S")["ss"] == 1.0
        assert VisemeMapper.MapPhoneme("K")["kk"] == 1.0
        assert VisemeMapper.MapPhoneme("R")["rr"] == 1.0

    def test_map_phoneme_case_insensitivity(self):
        """Test case insensitivity in phoneme mapping."""
        assert VisemeMapper.MapPhoneme("aa")["aa"] == 1.0
        assert VisemeMapper.MapPhoneme("Aa")["aa"] == 1.0
        assert VisemeMapper.MapPhoneme("th")["th"] == 1.0

    def test_map_phoneme_unknown_and_numbered(self):
        """Test unknown phonemes and numbered CMUDict stress phonemes (e.g. AA1, IH0)."""
        # Unknown phoneme defaults to viseme 0 (sil)
        w_unknown = VisemeMapper.MapPhoneme("UNKNOWN_XYZ")
        assert w_unknown["sil"] == 1.0

        w_empty = VisemeMapper.MapPhoneme("")
        assert w_empty["sil"] == 1.0

        # Note: CMUDict stress markers like AA1, IH0 are not stripped by MapPhoneme
        w_aa1 = VisemeMapper.MapPhoneme("AA1")
        # Since 'AA1' is not in table, it falls back to 0 -> 'sil'
        assert w_aa1["sil"] == 1.0
        assert w_aa1["aa"] == 0.0  # Documented finding!

    def test_map_phoneme_non_string_failure(self):
        """Test non-string inputs raise expected AttributeError because .upper() is called directly."""
        with pytest.raises(AttributeError):
            VisemeMapper.MapPhoneme(None)

        with pytest.raises(AttributeError):
            VisemeMapper.MapPhoneme(123)

    def test_map_oculus_viseme_valid(self):
        """Test standard Oculus visemes."""
        for shape in self.ALL_15_SHAPES:
            weights = VisemeMapper.MapOculusViseme(shape, intensity=1.0)
            assert set(weights.keys()) == set(self.ALL_15_SHAPES)
            assert weights[shape] == 1.0
            assert sum(v for k, v in weights.items() if k != shape) == 0.0

    def test_map_oculus_viseme_case_insensitivity(self):
        """Test case insensitivity in Oculus visemes."""
        w_upper = VisemeMapper.MapOculusViseme("AA", intensity=0.7)
        assert w_upper["aa"] == pytest.approx(0.7)

        w_mixed = VisemeMapper.MapOculusViseme("SiL", intensity=0.9)
        assert w_mixed["sil"] == pytest.approx(0.9)

    def test_map_oculus_viseme_unknown(self):
        """Test unknown Oculus viseme name produces all zeros."""
        w = VisemeMapper.MapOculusViseme("nonexistent_viseme")
        assert set(w.keys()) == set(self.ALL_15_SHAPES)
        assert all(v == 0.0 for v in w.values())

    def test_map_oculus_viseme_non_string_failure(self):
        """Test non-string input raises AttributeError because .lower() is called directly."""
        with pytest.raises(AttributeError):
            VisemeMapper.MapOculusViseme(None)

        with pytest.raises(AttributeError):
            VisemeMapper.MapOculusViseme(456)


# ============================================================================
# 4. Event Bus Integration Compatibility
# ============================================================================

class TestEventBusIntegration:
    """Stress tests verifying WakeWordEventData and UtteranceEventData work with EventBus."""

    @pytest.mark.asyncio
    async def test_publish_and_subscribe_wakeword(self):
        """Test async publish and receive of WakeWordEventData."""
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, handler)

        ww_data = WakeWordEventData(wake_word="Hey Serein", confidence=0.96)
        event = Event(type=CoreEvents.WAKE_WORD_DETECTED, data=ww_data)
        await bus.publish(event)

        assert len(received) == 1
        assert received[0].type == CoreEvents.WAKE_WORD_DETECTED
        assert isinstance(received[0].data, WakeWordEventData)
        assert received[0].data.wake_word == "Hey Serein"
        assert received[0].data.confidence == 0.96

    @pytest.mark.asyncio
    async def test_publish_and_subscribe_utterance(self):
        """Test async publish and receive of UtteranceEventData."""
        bus = EventBus()
        received = []

        def sync_handler(event: Event):
            received.append(event)

        bus.subscribe(CoreEvents.VOICE_INPUT, sync_handler)

        utt_data = UtteranceEventData(text="Turn on lights", confidence=0.91, audio_duration=1.2)
        event = Event(type=CoreEvents.VOICE_INPUT, data=utt_data)
        await bus.publish(event)

        # Allow sync worker thread task to finish
        assert len(received) == 1
        assert received[0].type == CoreEvents.VOICE_INPUT
        assert isinstance(received[0].data, UtteranceEventData)
        assert received[0].data.text == "Turn on lights"

    @pytest.mark.asyncio
    async def test_event_history_preserves_event_data(self):
        """Test EventBus history retains typed event data."""
        bus = EventBus()
        ww_data = WakeWordEventData(wake_word="Hey Serein", confidence=0.85)
        utt_data = UtteranceEventData(text="Testing history", confidence=0.99)

        await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=ww_data))
        await bus.publish(Event(type=CoreEvents.VOICE_INPUT, data=utt_data))

        history = bus.get_history()
        assert len(history) == 2
        assert history[0].data.wake_word == "Hey Serein"
        assert history[1].data.text == "Testing history"

        ww_history = bus.get_history(event_type=CoreEvents.WAKE_WORD_DETECTED)
        assert len(ww_history) == 1
        assert ww_history[0].data.wake_word == "Hey Serein"

    @pytest.mark.asyncio
    async def test_subscribe_typed_behavior(self):
        """Verify behavior of subscribe_typed with EventBus."""
        bus = EventBus()
        received = []

        async def typed_sub(payload):
            received.append(payload)

        bus.subscribe_typed(CoreEvents.WAKE_WORD_DETECTED, typed_sub)

        ww_data = WakeWordEventData(wake_word="Hey Serein", confidence=0.99)
        await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=ww_data))

        assert len(received) == 1
        # In current EventBus implementation, typed subscribers receive Event, not Event.data
        assert isinstance(received[0], Event)
        assert received[0].data == ww_data
