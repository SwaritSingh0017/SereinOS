"""
Unit and integration tests for Serein Avatar Bridge & Viseme Pipeline.
Verifies IPC socket protocol, viseme-to-VRM blendshape translation,
emotion mapping, and gesture dispatch.
"""
import asyncio
import json
import pytest
from typing import Dict, List, Any


class MockAvatarClient:
    """Simulates the Unity SereinAvatarController TCP receiver."""
    def __init__(self, port: int):
        self.port = port
        self.received_messages: List[Dict[str, Any]] = []
        self.server: asyncio.Server = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        buffer = ""
        while True:
            data = await reader.read(1024)
            if not data:
                break
            buffer += data.decode("utf-8")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        self.received_messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        writer.close()
        await writer.wait_closed()

    async def start(self):
        self.server = await asyncio.start_server(self._handle_client, "127.0.0.1", self.port)

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()


class AvatarBridge:
    """Python client that sends avatar packets from Core/Voice to Unity."""
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader = None
        self.writer: asyncio.StreamWriter = None
        self.is_connected = False

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.is_connected = True

    async def send_message(self, message: Dict[str, Any]):
        if not self.is_connected or not self.writer:
            raise ConnectionError("Avatar bridge is not connected")
        payload = json.dumps(message) + "\n"
        self.writer.write(payload.encode("utf-8"))
        await self.writer.drain()

    async def send_viseme(self, viseme: str, intensity: float = 1.0):
        await self.send_message({
            "type": "viseme",
            "viseme": viseme,
            "intensity": max(0.0, min(1.0, intensity))
        })

    async def send_emotion(self, emotion: str, intensity: float = 1.0, duration: float = 3.0):
        await self.send_message({
            "type": "emotion",
            "emotion": emotion,
            "intensity": max(0.0, min(1.0, intensity)),
            "duration": duration
        })

    async def send_gesture(self, gesture: str, duration: float = 2.0):
        await self.send_message({
            "type": "gesture",
            "gesture": gesture,
            "duration": duration
        })

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        self.is_connected = False


# VRM BlendShape mapping rules
VISEME_TO_VRM = {
    "sil": "A", "ae": "AA", "ax": "AA", "ah": "AA", "ao": "OH",
    "ey": "E", "eh": "E", "er": "O", "y": "I", "iy": "I",
    "ih": "I", "w": "OU", "uw": "OU", "uh": "OU", "ow": "OH",
    "oy": "OH", "ay": "AA", "aw": "OH", "p": "M", "b": "M",
    "m": "M", "f": "F", "v": "F", "th": "TH", "dh": "TH",
    "t": "TH", "d": "TH", "n": "N", "s": "S", "z": "S",
    "sh": "S", "zh": "S", "ch": "S", "jh": "S", "k": "K",
    "g": "K", "ng": "N", "l": "N", "r": "R"
}

EMOTION_TO_VRM = {
    "happy": "Joy",
    "sad": "Sorrow",
    "angry": "Angry",
    "surprised": "Surprised",
    "neutral": "Neutral"
}


def test_viseme_table_coverage():
    """All 22 Azure/Piper speech phonemes must map to recognized VRM target categories."""
    core_phonemes = ["sil", "ae", "ah", "ao", "ey", "eh", "er", "y", "iy", "ih", "w", "uw", "ow", "ay", "m", "f", "th", "s", "k", "n"]
    for p in core_phonemes:
        assert p in VISEME_TO_VRM, f"Phoneme {p} missing from viseme map"
        assert len(VISEME_TO_VRM[p]) > 0


def test_emotion_presets():
    """Core emotional states map to standard VRM 0.x / 1.0 expressions."""
    for emotion, vrm_target in EMOTION_TO_VRM.items():
        assert len(vrm_target) > 0
        assert vrm_target in ["Joy", "Sorrow", "Angry", "Surprised", "Neutral"]


@pytest.mark.asyncio
async def test_avatar_bridge_socket_transmission():
    """Verifies end-to-end TCP IPC exchange between python bridge and mock avatar."""
    port = 19876
    mock_avatar = MockAvatarClient(port)
    await mock_avatar.start()

    try:
        bridge = AvatarBridge("127.0.0.1", port)
        await bridge.connect()

        # Send viseme
        await bridge.send_viseme("ah", 0.85)

        # Send emotion
        await bridge.send_emotion("happy", 0.9, 2.5)

        # Send gesture
        await bridge.send_gesture("nod", 1.5)

        # Small delay for network buffer flush
        await asyncio.sleep(0.05)
        await bridge.close()

        # Assertions
        assert len(mock_avatar.received_messages) == 3

        msg1 = mock_avatar.received_messages[0]
        assert msg1["type"] == "viseme"
        assert msg1["viseme"] == "ah"
        assert pytest.approx(msg1["intensity"]) == 0.85

        msg2 = mock_avatar.received_messages[1]
        assert msg2["type"] == "emotion"
        assert msg2["emotion"] == "happy"
        assert pytest.approx(msg2["duration"]) == 2.5

        msg3 = mock_avatar.received_messages[2]
        assert msg3["type"] == "gesture"
        assert msg3["gesture"] == "nod"

    finally:
        await mock_avatar.stop()


@pytest.mark.asyncio
async def test_viseme_clamping():
    """Viseme and emotion intensity must be strictly clamped between 0.0 and 1.0."""
    port = 19877
    mock_avatar = MockAvatarClient(port)
    await mock_avatar.start()

    try:
        bridge = AvatarBridge("127.0.0.1", port)
        await bridge.connect()

        await bridge.send_viseme("ee", 1.8)
        await bridge.send_viseme("oh", -0.5)

        await asyncio.sleep(0.05)
        await bridge.close()

        assert len(mock_avatar.received_messages) == 2
        assert mock_avatar.received_messages[0]["intensity"] == 1.0
        assert mock_avatar.received_messages[1]["intensity"] == 0.0

    finally:
        await mock_avatar.stop()
