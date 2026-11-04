import asyncio
from pathlib import Path

import pytest

from serein_core.ai import BrainOutputError, BrainRouter, ChatMessage, ModelTier
from serein_core.inference import InferenceConfig
from serein_core.model_manager import RuntimePlan


class FakeEngine:
    def __init__(self, text='Hello from local Serein.'):
        self.text = text
        self.config = type('C', (), {'model_path': Path('/tmp/fake.gguf')})()

    @property
    def available(self):
        return True

    async def generate(self, messages, **kwargs):
        self.messages = messages
        return self.text

    def close(self):
        pass


def make_brain(fake):
    brain = BrainRouter.__new__(BrainRouter)
    brain._initialized = False
    brain._generation_lock = asyncio.Lock()
    brain.engine = fake
    brain.model_path = Path('/tmp/fake.gguf')
    brain.plan = RuntimePlan('standard', 'serein-local-small', 8192, 2048, 4, 0, False, True, 2)
    brain.selected_model = None
    brain.model_manager = None
    return brain


def test_brain_rejects_empty_messages():
    brain = make_brain(FakeEngine())
    with pytest.raises(ValueError):
        asyncio.run(brain.chat([]))


def test_brain_generates_local_response():
    brain = make_brain(FakeEngine())
    result = asyncio.run(brain.chat([ChatMessage('user', 'Hello')], tier=ModelTier.LOCAL))
    assert result.content == 'Hello from local Serein.'
    assert result.provider == 'local-llama.cpp'
    assert result.model == 'serein-local-small'


def test_brain_parses_safe_tool_proposal():
    response = make_brain(FakeEngine('{"content":"Okay","tool_calls":[{"name":"set_alarm","arguments":{"time":"20:00"}}]}'))
    result = asyncio.run(response.propose_action([ChatMessage('user', 'Set an alarm')], allowed_tools=['set_alarm']))
    assert result.content == 'Okay'
    assert result.has_tool_calls
    assert result.tool_calls[0].name == 'set_alarm'
    assert result.tool_calls[0].arguments['time'] == '20:00'


def test_brain_rejects_invalid_action_json():
    brain = make_brain(FakeEngine('not json'))
    with pytest.raises(BrainOutputError):
        asyncio.run(brain.propose_action([ChatMessage('user', 'Do it')], allowed_tools=['x']))
