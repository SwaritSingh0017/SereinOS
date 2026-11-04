"""
Test Suite for SereinOS Local Conversational Brain Engine & API Integration.
Verifies slang normalization, math evaluation, offline dialogue, and zero robotic fallback.
"""

import pytest
from starlette.testclient import TestClient

from serein_core.local_brain import LocalBrainEngine, get_local_brain
from serein_core.web_app import app


@pytest.fixture
def brain() -> LocalBrainEngine:
    return get_local_brain()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestLocalBrainEngine:
    """Test unit capabilities of the offline Local Brain."""

    def test_slang_normalization(self, brain: LocalBrainEngine):
        """Verify texting slang is expanded to canonical intent keys."""
        assert brain.normalize_text("who r u") == "who are you"
        assert brain.normalize_text("who are u") == "who are you"
        assert brain.normalize_text("what r u") == "what are you"
        assert brain.normalize_text("how r u") == "how are you"
        assert brain.normalize_text("thx") == "thank you"
        assert brain.normalize_text("cya") == "see you"

    def test_identity_queries(self, brain: LocalBrainEngine):
        """Verify Serein answers identity questions warmly in character."""
        for q in ["who r u", "who are you", "who u", "tell me about yourself", "what are you"]:
            res = brain.generate_local_response(q)
            assert "I am Serein" in res["content"]
            assert res["emotion"] == "happy"
            assert res["user"] == "Kai"

    def test_capabilities_queries(self, brain: LocalBrainEngine):
        """Verify Serein describes her capabilities without robotic text."""
        for q in ["what can u do", "what can you do", "help", "features", "commands"]:
            res = brain.generate_local_response(q)
            assert "terminal" in res["content"].lower() or "explore" in res["content"].lower()
            assert "logged" not in res["content"].lower()

    def test_status_and_greetings(self, brain: LocalBrainEngine):
        """Verify greetings and wellness inquiries."""
        res_hi = brain.generate_local_response("hi serein")
        assert "Kai" in res_hi["content"]

        res_hru = brain.generate_local_response("how r u")
        assert "wonderful" in res_hru["content"].lower() or "ready" in res_hru["content"].lower()

    def test_mathematical_reasoning(self, brain: LocalBrainEngine):
        """Verify safe mathematical evaluation."""
        assert brain.evaluate_math("15 * 8") == "Calculation result: 15 * 8 = 120"
        assert brain.evaluate_math("what is 100 / 4") == "Calculation result: 100 / 4 = 25"
        assert brain.evaluate_math("sqrt(144)") == "Calculation result: sqrt(144) = 12"
        assert brain.evaluate_math("hello world") is None

    def test_hardware_status_inquiry(self, brain: LocalBrainEngine):
        """Verify live hardware diagnostics return real system specs."""
        res = brain.generate_local_response("specs")
        assert "Live System Status" in res["content"]
        assert "CPU load" in res["content"]
        assert "memory" in res["content"].lower()

    def test_zero_robotic_fallback(self, brain: LocalBrainEngine):
        """Verify arbitrary natural queries NEVER return the robotic dummy string."""
        arbitrary_queries = [
            "what do you think about the universe?",
            "can we build something beautiful today?",
            "why is the sky blue?",
            "tell me your favorite thought",
        ]
        for q in arbitrary_queries:
            res = brain.generate_local_response(q)
            content = res["content"]
            # Strict guarantee: NEVER use the old robotic phrase
            assert "logged" not in content
            assert "working memory" not in content
            assert "Kai" in content
            assert len(content) > 20


class TestChatApiEndpoint:
    """Test HTTP API /api/chat integration with local brain."""

    def test_chat_slang_identity(self, client: TestClient):
        """Verify POST /api/chat with 'who r u' returns Serein companion response."""
        res = client.post("/api/chat", json={"message": "who r u"})
        assert res.status_code == 200
        data = res.json()
        assert "I am Serein" in data["content"]
        assert data["user"] == "Kai"
        assert "logged" not in data["content"]

    def test_chat_math_expression(self, client: TestClient):
        """Verify POST /api/chat solves arithmetic expressions."""
        res = client.post("/api/chat", json={"message": "what is 25 * 4"})
        assert res.status_code == 200
        data = res.json()
        assert "100" in data["content"]

    def test_chat_system_status(self, client: TestClient):
        """Verify POST /api/chat responds with live system info."""
        res = client.post("/api/chat", json={"message": "system status"})
        assert res.status_code == 200
        data = res.json()
        assert "Live System Status" in data["content"]
