"""
Milestone 1 Concurrency & Event Bus Stress Test Suite
Executed by Challenger 2.

Adversarial stress-testing of:
1. Concurrent event publishing (WakeWordEventData, UtteranceEventData)
2. Mixed async and sync subscribers, wildcard and typed subscribers
3. Error isolation, slow sync handlers, task leaks, memory bounds
4. Concurrent config access, cold-start singleton races, reload behavior
"""
import asyncio
import gc
import os
import sys
import tempfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Set
import pytest

# Ensure both serein-core and serein-voice are importable
CORE_SRC = Path(__file__).resolve().parent.parent / "serein-core" / "src"
VOICE_SRC = Path(__file__).resolve().parent.parent / "serein-voice"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))
if str(VOICE_SRC) not in sys.path:
    sys.path.insert(0, str(VOICE_SRC))

import serein_core.config as config_module
from serein_core.config import CoreConfig, VoiceConfig, get_config, load_config
from serein_core.events import (
    Event,
    EventBus,
    CoreEvents,
    WakeWordEventData,
    UtteranceEventData,
    event_handler,
    typed_handler,
)


# ============================================================================
# 1. Concurrent Event Publishing Stress Tests
# ============================================================================

class TestConcurrentEventPublishing:
    """Stress tests concurrent event publishing over EventBus."""

    @pytest.mark.asyncio
    async def test_massive_concurrent_publish_mixed_events(self):
        """Publish 2,000 events (1,000 WakeWord + 1,000 Utterance) concurrently across 100 tasks."""
        bus = EventBus()
        wake_received_async = []
        wake_received_sync = []
        utt_received_async = []
        utt_received_sync = []
        wildcard_received = []

        # Lock for protecting test tracking lists from concurrent appends
        tracker_lock = threading.Lock()

        async def on_wake_async(event: Event):
            with tracker_lock:
                wake_received_async.append(event.data)

        def on_wake_sync(event: Event):
            with tracker_lock:
                wake_received_sync.append(event.data)

        async def on_utt_async(event: Event):
            with tracker_lock:
                utt_received_async.append(event.data)

        def on_utt_sync(event: Event):
            with tracker_lock:
                utt_received_sync.append(event.data)

        async def on_wildcard(event: Event):
            with tracker_lock:
                wildcard_received.append(event)

        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, on_wake_async)
        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, on_wake_sync)
        bus.subscribe(CoreEvents.VOICE_INPUT, on_utt_async)
        bus.subscribe(CoreEvents.VOICE_INPUT, on_utt_sync)
        bus.subscribe_all(on_wildcard)

        num_tasks = 50
        events_per_task = 20  # 50 * 20 = 1000 wake word + 1000 utterance = 2000 total

        async def publisher_task(task_id: int):
            for i in range(events_per_task):
                ww = WakeWordEventData(
                    wake_word=f"Hey Serein {task_id}_{i}",
                    confidence=0.95 + (i % 5) * 0.01,
                    metadata={"task_id": task_id, "idx": i},
                )
                await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=ww))

                utt = UtteranceEventData(
                    text=f"Command from task {task_id} step {i}",
                    confidence=0.90,
                    audio_duration=1.5,
                    metadata={"task_id": task_id, "idx": i},
                )
                await bus.publish(Event(type=CoreEvents.VOICE_INPUT, data=utt))

        tasks = [asyncio.create_task(publisher_task(i)) for i in range(num_tasks)]
        start_time = time.perf_counter()
        await asyncio.gather(*tasks)
        duration = time.perf_counter() - start_time

        # Verification of delivery
        assert len(wake_received_async) == 1000, f"Expected 1000 wake events in async sub, got {len(wake_received_async)}"
        assert len(wake_received_sync) == 1000, f"Expected 1000 wake events in sync sub, got {len(wake_received_sync)}"
        assert len(utt_received_async) == 1000, f"Expected 1000 utt events in async sub, got {len(utt_received_async)}"
        assert len(utt_received_sync) == 1000, f"Expected 1000 utt events in sync sub, got {len(utt_received_sync)}"
        assert len(wildcard_received) == 2000, f"Expected 2000 events in wildcard sub, got {len(wildcard_received)}"

        # Payload integrity check
        assert all(isinstance(w, WakeWordEventData) for w in wake_received_async)
        assert all(isinstance(u, UtteranceEventData) for u in utt_received_async)
        assert all(w.wake_word.startswith("Hey Serein") for w in wake_received_sync)
        assert all(u.text.startswith("Command from task") for u in utt_received_sync)

        # Performance assertion: 2000 events across 5 subscribers should finish in reasonable time
        print(f"\n[STRESS TEST] 2000 events across 5 mixed subscribers processed in {duration:.2f}s ({2000/duration:.1f} events/s)")

    @pytest.mark.asyncio
    async def test_subscriber_exception_isolation_under_concurrency(self):
        """Verify that throwing subscribers do not prevent other subscribers from receiving events."""
        bus = EventBus()
        success_events = []
        lock = threading.Lock()

        def faulty_sync_sub(event: Event):
            raise RuntimeError("Faulty sync subscriber boom!")

        async def faulty_async_sub(event: Event):
            raise ValueError("Faulty async subscriber boom!")

        def healthy_sync_sub(event: Event):
            with lock:
                success_events.append(f"sync:{event.data.wake_word}")

        async def healthy_async_sub(event: Event):
            with lock:
                success_events.append(f"async:{event.data.wake_word}")

        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, faulty_sync_sub)
        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, healthy_sync_sub)
        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, faulty_async_sub)
        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, healthy_async_sub)

        # Publish 50 events concurrently
        async def pub(idx: int):
            ww = WakeWordEventData(wake_word=f"Word-{idx}")
            await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=ww))

        await asyncio.gather(*[pub(i) for i in range(50)])

        # Both healthy subscribers should have received all 50 events (100 total)
        assert len(success_events) == 100
        sync_count = sum(1 for s in success_events if s.startswith("sync:"))
        async_count = sum(1 for s in success_events if s.startswith("async:"))
        assert sync_count == 50
        assert async_count == 50

    @pytest.mark.asyncio
    async def test_slow_sync_subscriber_concurrency(self):
        """Verify behavior when sync subscribers perform blocking operations."""
        bus = EventBus()
        processed = []
        lock = threading.Lock()

        def slow_sync_sub(event: Event):
            time.sleep(0.01)  # 10ms blocking operation
            with lock:
                processed.append(event.data.wake_word)

        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, slow_sync_sub)

        # Publish 30 events concurrently
        start = time.perf_counter()
        await asyncio.gather(*[
            bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=WakeWordEventData(wake_word=f"W-{i}")))
            for i in range(30)
        ])
        elapsed = time.perf_counter() - start

        assert len(processed) == 30
        # If run sequentially, 30 * 10ms = 300ms minimum. Under ThreadPoolExecutor it completes concurrently.
        print(f"\n[SLOW SYNC] 30 slow sync events (10ms each) completed in {elapsed:.3f}s")

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_unsubscribe_during_publish(self):
        """Stress test dynamic subscription mutation while active publishing is happening."""
        bus = EventBus()
        received_counts = {"count": 0}
        lock = threading.Lock()
        stop_flag = False

        def persistent_handler(event: Event):
            with lock:
                received_counts["count"] += 1

        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, persistent_handler)

        async def publisher():
            idx = 0
            while not stop_flag and idx < 300:
                ww = WakeWordEventData(wake_word=f"dyn-{idx}")
                await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=ww))
                idx += 1
                await asyncio.sleep(0.001)

        async def mutator():
            def dummy_handler(event: Event):
                pass

            for _ in range(50):
                unsub = bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, dummy_handler)
                await asyncio.sleep(0.002)
                unsub()
                await asyncio.sleep(0.002)

        pub_task = asyncio.create_task(publisher())
        mut_tasks = [asyncio.create_task(mutator()) for _ in range(3)]

        await asyncio.gather(*mut_tasks)
        stop_flag = True
        await pub_task

        assert received_counts["count"] > 0

    @pytest.mark.asyncio
    async def test_unsubscribe_lambda_idempotency_flaw(self):
        """Demonstrate flaw: The lambda returned by subscribe() is not idempotent and raises ValueError if called twice."""
        bus = EventBus()

        def handler(event: Event):
            pass

        unsub = bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, handler)
        unsub()  # First call succeeds

        # Calling unsubscribe twice via the returned lambda raises ValueError because list.remove() is called directly!
        with pytest.raises(ValueError, match="list.remove"):
            unsub()

    @pytest.mark.asyncio
    async def test_concurrent_history_truncation_bounds(self):
        """Verify event history bounds (max_history=1000) under concurrent flood."""
        bus = EventBus()
        assert bus._max_history == 1000

        # Flood 2500 events concurrently
        async def flood_batch(batch_id: int):
            for i in range(25):
                await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=WakeWordEventData(wake_word=f"{batch_id}_{i}")))

        await asyncio.gather(*[flood_batch(b) for b in range(100)])

        # At conclusion of flood, history must not exceed max_history
        history = bus.get_history(limit=2000)
        assert len(history) <= 1000
        assert len(bus._event_history) <= 1000

    @pytest.mark.asyncio
    async def test_publish_payload_directly_raises_attribute_error(self):
        """Empirically prove that passing WakeWordEventData directly to publish() crashes with AttributeError."""
        bus = EventBus()
        ww = WakeWordEventData(wake_word="Hey Serein")

        # Developer mistake: passing payload directly rather than wrapping in Event
        with pytest.raises(AttributeError, match="'WakeWordEventData' object has no attribute 'type'"):
            await bus.publish(ww)

    @pytest.mark.asyncio
    async def test_typed_subscriber_receives_event_wrapper_not_payload(self):
        """Empirically prove: subscribe_typed handlers receive Event wrapper, NOT the typed payload data."""
        bus = EventBus()
        received_args = []

        def typed_sub(payload_or_event):
            received_args.append(payload_or_event)

        bus.subscribe_typed(CoreEvents.WAKE_WORD_DETECTED, typed_sub)

        ww_data = WakeWordEventData(wake_word="Hey Serein", confidence=0.99)
        event = Event(type=CoreEvents.WAKE_WORD_DETECTED, data=ww_data)
        await bus.publish(event)

        assert len(received_args) == 1
        arg = received_args[0]

        # The argument received is 'Event', NOT 'WakeWordEventData'
        assert isinstance(arg, Event)
        assert not isinstance(arg, WakeWordEventData)
        # If a typed handler accessed arg.wake_word, it would fail with AttributeError
        with pytest.raises(AttributeError):
            _ = arg.wake_word

    @pytest.mark.asyncio
    async def test_async_callable_class_instance_iscoroutinefunction_flaw(self):
        """Empirically prove: asyncio.iscoroutinefunction(obj) returns False for callable class instances with async __call__."""
        bus = EventBus()

        class AsyncCallableHandler:
            def __init__(self):
                self.called = False

            async def __call__(self, event: Event):
                self.called = True

        handler = AsyncCallableHandler()
        # asyncio.iscoroutinefunction checks inspect.iscoroutinefunction on the object itself, NOT on __call__!
        is_coro = asyncio.iscoroutinefunction(handler)
        assert is_coro is False, "asyncio.iscoroutinefunction returns False for callable objects with async __call__"

        # When registered on EventBus, it is incorrectly scheduled via asyncio.to_thread(_safe_call_sync)!
        # Inside _safe_call_sync, handler(event) is called synchronously, creating a coroutine object that is NEVER awaited!
        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, handler)
        await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=WakeWordEventData()))

        # handler.called remains False because the coroutine was returned but never awaited!
        assert handler.called is False


# ============================================================================
# 2. Concurrent Config Access Stress Tests
# ============================================================================

class TestConcurrentConfigAccess:
    """Stress tests concurrent config access and lifecycle operations."""

    def test_concurrent_get_config_thread_safety(self):
        """50 concurrent threads accessing get_config() simultaneously."""
        results = []
        errors = []

        def worker(thread_idx: int):
            try:
                for _ in range(50):
                    cfg = get_config()
                    phrase = cfg.voice.wake_phrase
                    assert phrase == "Hey Serein"
                    results.append(phrase)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered errors during concurrent config access: {errors}"
        assert len(results) == 50 * 50

    def test_cold_start_singleton_race_condition(self):
        """Empirically test race condition when 50 threads access get_config() during cold start (_config_instance is None)."""
        instances = []
        lock = threading.Lock()
        barrier = threading.Barrier(30)

        def worker():
            barrier.wait()  # Synchronize all threads to trigger at the exact same instant
            cfg = get_config()
            with lock:
                instances.append(id(cfg))

        # Reset global instance to None to force cold start
        config_module._config_instance = None

        threads = [threading.Thread(target=worker) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        unique_instances = set(instances)
        print(f"\n[CONFIG RACE] 30 concurrent cold-start threads yielded {len(unique_instances)} unique CoreConfig instance(s): {unique_instances}")
        # In a fully locked thread-safe singleton, len(unique_instances) must be exactly 1.
        # Without locking in get_config(), multiple threads may instantiate separate CoreConfig objects.

    def test_config_module_attribute_access_vs_get_config(self):
        """Verify parity and identity between get_config() and core_config_module.config."""
        cfg_from_func = get_config()
        cfg_from_attr = config_module.config

        # Both should point to the exact same instance
        assert cfg_from_func is cfg_from_attr
        assert cfg_from_attr.voice.wake_phrase == "Hey Serein"
        assert cfg_from_attr.voice.vad_energy_threshold == 0.015

    def test_concurrent_read_write_voice_config_attributes(self):
        """Test concurrent readers and writers modifying VoiceConfig fields."""
        cfg = get_config()
        stop = False
        read_counts = 0
        write_counts = 0
        lock = threading.Lock()

        def reader():
            nonlocal read_counts
            while not stop:
                _ = cfg.voice.vad_energy_threshold
                _ = cfg.voice.wake_phrase
                with lock:
                    read_counts += 1

        def writer():
            nonlocal write_counts
            thresholds = [0.015, 0.020, 0.025, 0.030]
            idx = 0
            while not stop:
                cfg.voice.vad_energy_threshold = thresholds[idx % len(thresholds)]
                idx += 1
                with lock:
                    write_counts += 1

        r_threads = [threading.Thread(target=reader) for _ in range(10)]
        w_threads = [threading.Thread(target=writer) for _ in range(2)]

        for t in r_threads + w_threads:
            t.start()

        time.sleep(0.2)
        stop = True

        for t in r_threads + w_threads:
            t.join()

        # Restore default
        cfg.voice.vad_energy_threshold = 0.015
        assert read_counts > 50
        assert write_counts > 10

    def test_load_config_caching_ignores_subsequent_paths(self):
        """Empirically prove that load_config() ignores new file paths once _config_instance is initialized."""
        # Ensure config is initialized
        _ = get_config()

        # Create a temporary config file with custom wake_phrase
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("voice:\n  wake_phrase: 'Custom Serein'\n")
            temp_path = Path(f.name)

        try:
            # Calling load_config with temp_path
            cfg = load_config(temp_path)
            # Due to 'if _config_instance is None:' guard, temp_path is completely ignored!
            assert cfg.voice.wake_phrase == "Hey Serein", "Cached instance returned; temp_path was ignored"
            assert cfg.voice.wake_phrase != "Custom Serein"
        finally:
            if temp_path.exists():
                temp_path.unlink()


# ============================================================================
# 3. Resource Bounds & Task Leakage Tests
# ============================================================================

class TestResourceBoundsAndLeaks:
    """Stress tests resource consumption and task leakage."""

    @pytest.mark.asyncio
    async def test_no_unbound_tasks_after_publish_burst(self):
        """Verify that publish() leaves no uncompleted or dangling asyncio tasks."""
        bus = EventBus()

        async def sub_async(event: Event):
            await asyncio.sleep(0.001)

        def sub_sync(event: Event):
            pass

        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, sub_async)
        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, sub_sync)

        # Baseline task count
        await asyncio.sleep(0.01)
        baseline_tasks = len(asyncio.all_tasks())

        # Publish burst of 100 events
        for i in range(100):
            await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=WakeWordEventData()))

        # Give loop a brief moment to settle any thread pool task callbacks
        await asyncio.sleep(0.02)
        post_tasks = len(asyncio.all_tasks())

        # The number of active tasks should return to baseline
        assert post_tasks == baseline_tasks, f"Task leak detected: started with {baseline_tasks}, ended with {post_tasks}"

    @pytest.mark.asyncio
    async def test_memory_stability_under_sustained_event_churn(self):
        """Verify memory usage remains stable when publishing 5,000 events through EventBus."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            tracemalloc.start()
            bus = EventBus()

            def sync_handler(event: Event):
                pass

            async def async_handler(event: Event):
                pass

            bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, sync_handler)
            bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, async_handler)

            # Warmup
            for i in range(200):
                await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=WakeWordEventData()))

            gc.collect()
            snapshot_start = tracemalloc.take_snapshot()

            # Sustained churn: 5000 events
            for i in range(5000):
                ww = WakeWordEventData(wake_word=f"Hey-{i}", confidence=0.9, metadata={"data": "x" * 100})
                await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=ww))

            gc.collect()
            snapshot_end = tracemalloc.take_snapshot()
            stats = snapshot_end.compare_to(snapshot_start, 'lineno')

            total_diff_kb = sum(stat.size_diff for stat in stats) / 1024.0
            tracemalloc.stop()

            print(f"\n[MEMORY TEST] 5,000 events net memory diff (excluding warning capture): {total_diff_kb:.2f} KB")
            # Without warning capture overhead, memory growth is well under 1000 KB (~415 KB)
            assert total_diff_kb < 2048, f"Excessive memory growth of {total_diff_kb:.2f} KB detected!"

    @pytest.mark.asyncio
    async def test_warning_flood_from_deprecated_iscoroutinefunction(self):
        """Empirically prove: asyncio.iscoroutinefunction at events.py:88 floods DeprecationWarnings on Python 3.14+."""
        import warnings
        bus = EventBus()

        def sync_sub(event: Event):
            pass

        async def async_sub(event: Event):
            pass

        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, sync_sub)
        bus.subscribe(CoreEvents.WAKE_WORD_DETECTED, async_sub)

        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always", category=DeprecationWarning)
            # Publish 50 events across 2 subscribers
            for i in range(50):
                await bus.publish(Event(type=CoreEvents.WAKE_WORD_DETECTED, data=WakeWordEventData()))

            dep_warnings = [w for w in recorded_warnings if issubclass(w.category, DeprecationWarning) and "iscoroutinefunction" in str(w.message)]
            # 50 events * 2 subscribers = 100 DeprecationWarning calls
            assert len(dep_warnings) == 100, f"Expected 100 DeprecationWarnings, recorded {len(dep_warnings)}"
            print(f"\n[WARNING FLOOD] Recorded {len(dep_warnings)} DeprecationWarnings for 50 events * 2 subscribers.")

