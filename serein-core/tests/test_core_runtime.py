from pathlib import Path

from serein_core.inference import InferenceConfig, resolve_model_path
from serein_core.model_manager import RuntimePlan
from serein_core.performance import PerformanceEngine


def test_performance_engine_creates_deterministic_greeting_scene():
    scene = PerformanceEngine().compose(
        "Hello! It is good to see you.",
        emotion="happy",
        intensity=0.8,
        intent="greeting",
    )

    assert scene.speech.text.startswith("Hello!")
    assert scene.expression.name == "gentle_smile"
    assert scene.gesture.name == "small_wave"
    assert scene.gaze.target == "user"
    assert scene.posture == "relaxed"


def test_performance_engine_clamps_emotion_intensity():
    scene = PerformanceEngine().compose("Done.", emotion="neutral", intensity=4)
    assert scene.emotion_intensity == 1.0
    assert scene.expression.intensity == 1.0


def test_inference_path_resolution():
    path = resolve_model_path("serein-local-tiny", "/models")
    assert path == Path("/models/serein-tiny.gguf")


def test_inference_config_uses_runtime_plan():
    plan = RuntimePlan(
        hardware_tier="minimum",
        model_id="serein-local-tiny",
        max_context_tokens=4096,
        max_output_tokens=1024,
        threads=2,
        gpu_layers=0,
        enable_vision=False,
        enable_complex_agents=False,
        concurrency=1,
    )
    config = InferenceConfig.from_plan(plan, "/models/serein-tiny.gguf")
    assert config.context_tokens == 4096
    assert config.threads == 2
    assert config.model_path == Path("/models/serein-tiny.gguf")
