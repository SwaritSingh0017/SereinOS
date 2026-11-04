import asyncio

from serein_core.intent import IntentClassifier, ToolRegistry


def test_intent_classifier_handles_reminders_and_entities():
    result = IntentClassifier().classify("Remind me to call Mom at 8 PM tomorrow")
    assert result.intent == "reminder"
    assert result.confidence >= 0.9
    assert result.entities["time"].lower() == "8 pm"
    assert result.entities["date"] == "tomorrow"
    assert "call mom" in result.entities["task"]


def test_intent_classifier_marks_destructive_system_action():
    result = IntentClassifier().classify("shutdown the computer")
    assert result.intent == "system_control"
    assert result.entities["action"] == "shutdown"
    assert result.requires_confirmation is True


def test_intent_classifier_extracts_app_control():
    result = IntentClassifier().classify("open Chrome")
    assert result.intent == "app_control"
    assert result.entities["action"] == "open"
    assert result.entities["application"] == "chrome"
    assert result.requires_confirmation is False


def test_intent_classifier_extracts_search_query():
    result = IntentClassifier().classify("search for the weather tomorrow")
    assert result.intent == "search"
    assert result.entities["query"] == "the weather tomorrow"


def test_intent_classifier_extracts_file_operation():
    result = IntentClassifier().classify("delete the report file")
    assert result.intent == "file_operation"
    assert result.entities["operation"] == "delete"
    assert result.requires_confirmation is True


def test_intent_classifier_normalizes_unknown_and_empty_input():
    empty = IntentClassifier().classify("   ")
    assert empty.intent == "unknown"
    assert empty.confidence == 0.0

    conversation = IntentClassifier().classify("I was just thinking about the project")
    assert conversation.intent == "conversation"


def test_tool_registry_supports_async_tools():
    async def add(a: int, b: int) -> int:
        return a + b

    async def run():
        registry = ToolRegistry()
        registry.register("add", "Add numbers.", add)
        result = await registry.execute("add", a=2, b=3)
        assert result.success is True
        assert result.result == 5

    asyncio.run(run())


def test_tool_registry_requires_confirmation():
    async def erase() -> str:
        return "erased"

    async def run():
        registry = ToolRegistry()
        registry.register("erase", "Erase data.", erase, requires_confirmation=True)
        blocked = await registry.execute("erase")
        assert blocked.success is False
        assert "Confirmation required" in blocked.error

        allowed = await registry.execute("erase", confirmation=True)
        assert allowed.success is True

    asyncio.run(run())
