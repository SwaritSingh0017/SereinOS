import pytest

from serein_core.decision import ActionType, DecisionEngine, Route
from serein_core.intent import IntentClassifier
from serein_core.intent.classifier import IntentResult


@pytest.mark.asyncio
async def test_question_routes_to_brain():
    engine = DecisionEngine()
    result = IntentClassifier().classify("What is a process in an operating system?")
    decision = await engine.decide(result)
    assert decision.action is ActionType.RESPOND
    assert decision.route is Route.BRAIN


@pytest.mark.asyncio
async def test_reminder_routes_to_scheduler():
    engine = DecisionEngine()
    result = IntentClassifier().classify("Remind me to submit my assignment at 8 PM")
    decision = await engine.decide(result)
    assert decision.action is ActionType.SCHEDULE
    assert decision.route is Route.SCHEDULER


@pytest.mark.asyncio
async def test_system_control_routes_to_command_bus():
    engine = DecisionEngine()
    result = IntentClassifier().classify("open Chrome")
    decision = await engine.decide(result)
    assert decision.action is ActionType.EXECUTE
    assert decision.route is Route.COMMAND_BUS


@pytest.mark.asyncio
async def test_destructive_action_requires_confirmation_before_routing():
    engine = DecisionEngine()
    result = IntentClassifier().classify("shutdown the computer")
    decision = await engine.decide(result)
    assert decision.action is ActionType.CLARIFY
    assert decision.route is Route.SAFETY
    assert decision.requires_confirmation is True

    confirmed = await engine.decide(result, user_confirmation=True)
    assert confirmed.action is ActionType.EXECUTE
    assert confirmed.route is Route.COMMAND_BUS


@pytest.mark.asyncio
async def test_safety_block_has_priority():
    engine = DecisionEngine()
    result = IntentResult("system_control", 0.99)
    decision = await engine.decide(result, safety={"allowed": False, "reason": "blocked"})
    assert decision.route is Route.SAFETY
    assert decision.action is ActionType.RESPOND


@pytest.mark.asyncio
async def test_low_confidence_intent_requests_clarification():
    engine = DecisionEngine()
    result = IntentResult("unknown", 0.1)
    decision = await engine.decide(result)
    assert decision.action is ActionType.CLARIFY
    assert decision.metadata["clarification_required"] is True


def test_decision_serialization():
    result = IntentResult("reminder", 0.95, {"task": "study"})
    engine = DecisionEngine()
    decision = engine._route(result, {})
    payload = decision.to_dict()
    assert payload["route"] == "scheduler"
    assert payload["entities"]["task"] == "study"
