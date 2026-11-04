import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from serein_core.scheduler import (
    EventStatus,
    ScheduledEvent,
    Scheduler,
    TriggerType,
)


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.migrations = []

    async def execute(self, sql, params=()):
        sql_lower = sql.lower()
        if "alter table scheduled_events" in sql_lower:
            self.migrations.append(sql)
            return
        if "insert into scheduled_events" in sql_lower:
            row = dict(zip(
                ["id", "trigger_type", "fires_at", "recurrence_rule",
                 "condition_spec", "message", "status", "created_at",
                 "fired_at", "payload"],
                params,
            ))
            self.rows[row["id"]] = row

    async def fetch_all(self, sql, params=()):
        return list(self.rows.values())


class FakeEventBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class FakeCommandBus:
    def __init__(self):
        self.calls = []

    async def execute(self, command, params):
        self.calls.append((command, params))

        class Result:
            success = True
            error = None

        return Result()


def now():
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_schedule_one_time_reminder():
    db = FakeDB()
    events = FakeEventBus()
    scheduler = Scheduler(db=db, event_bus_instance=events, now_fn=now)

    when = now() + timedelta(minutes=5)
    event_id = await scheduler.remind_at("Study DSA", when)

    event = await scheduler.get_event(event_id)
    assert event is not None
    assert event.message == "Study DSA"
    assert event.status is EventStatus.PENDING
    assert event_id in db.rows


@pytest.mark.asyncio
async def test_relative_reminder_requires_positive_delay():
    scheduler = Scheduler(db=FakeDB(), now_fn=now)

    with pytest.raises(ValueError):
        await scheduler.remind_in("test", timedelta(seconds=0))


@pytest.mark.asyncio
async def test_event_fires_once():
    db = FakeDB()
    events = FakeEventBus()
    clock = [now()]
    scheduler = Scheduler(
        db=db,
        event_bus_instance=events,
        now_fn=lambda: clock[0],
    )

    event_id = await scheduler.remind_at("Hello", clock[0] + timedelta(seconds=1))
    clock[0] += timedelta(seconds=2)
    await scheduler._check_events(clock[0])

    event = await scheduler.get_event(event_id)
    assert event.status is EventStatus.FIRED
    assert any(e.type == "core.scheduler.event_fired" for e in events.events)


@pytest.mark.asyncio
async def test_recurring_event_survives_and_advances():
    db = FakeDB()
    clock = [datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)]
    scheduler = Scheduler(
        db=db,
        now_fn=lambda: clock[0],
    )

    event_id = await scheduler.remind_recurring("Daily", "0 19 * * *")
    assert event_id in scheduler._next_fire
    first = scheduler._next_fire[event_id]

    clock[0] = first
    await scheduler._check_events(clock[0])

    event = await scheduler.get_event(event_id)
    assert event.status is EventStatus.PENDING
    assert scheduler._next_fire[event_id] > first


@pytest.mark.asyncio
async def test_condition_event_fires():
    db = FakeDB()
    events = FakeEventBus()
    scheduler = Scheduler(db=db, event_bus_instance=events, now_fn=now)

    event_id = await scheduler.remind_when(
        "Condition met",
        {"type": "always"},
    )
    await scheduler._check_conditions()

    event = await scheduler.get_event(event_id)
    assert event.status is EventStatus.FIRED


@pytest.mark.asyncio
async def test_custom_action_goes_through_command_bus():
    db = FakeDB()
    command_bus = FakeCommandBus()
    scheduler = Scheduler(
        db=db,
        command_bus=command_bus,
        now_fn=now,
    )

    event_id = await scheduler.remind_at(
        "Run task",
        now() + timedelta(seconds=1),
        action="launch_app",
        params={"app": "python"},
    )
    scheduler._events[event_id].fires_at = now() - timedelta(seconds=1)
    await scheduler._check_events(now())

    assert command_bus.calls == [
        ("launch_app", {"app": "python"})
    ]


@pytest.mark.asyncio
async def test_cancel_event():
    db = FakeDB()
    scheduler = Scheduler(db=db, now_fn=now)

    event_id = await scheduler.remind_at(
        "Cancel me",
        now() + timedelta(minutes=5),
    )
    assert await scheduler.cancel_event(event_id)
    event = await scheduler.get_event(event_id)
    assert event.status is EventStatus.CANCELLED
    assert event_id not in scheduler._next_fire


@pytest.mark.asyncio
async def test_payload_is_persisted():
    db = FakeDB()
    scheduler = Scheduler(db=db, now_fn=now)

    event_id = await scheduler.remind_at(
        "Persistent action",
        now() + timedelta(minutes=5),
        action="launch_app",
        params={"app": "python"},
    )

    assert '"action": "launch_app"' in db.rows[event_id]["payload"]
    assert '"app": "python"' in db.rows[event_id]["payload"]


@pytest.mark.asyncio
async def test_scheduler_loads_recurring_and_conditional_events():
    db = FakeDB()
    created = now()
    db.rows["r1"] = {
        "id": "r1",
        "trigger_type": "recurring",
        "fires_at": None,
        "recurrence_rule": "0 19 * * *",
        "condition_spec": "{}",
        "message": "daily",
        "status": "pending",
        "created_at": created.isoformat(),
        "fired_at": None,
        "payload": "{}",
    }
    db.rows["c1"] = {
        "id": "c1",
        "trigger_type": "conditional",
        "fires_at": None,
        "recurrence_rule": None,
        "condition_spec": '{"type":"always"}',
        "message": "conditional",
        "status": "pending",
        "created_at": created.isoformat(),
        "fired_at": None,
        "payload": "{}",
    }

    scheduler = Scheduler(db=db, now_fn=now)
    await scheduler._load_events()

    assert "r1" in scheduler._events
    assert "c1" in scheduler._events


@pytest.mark.asyncio
async def test_start_stop_is_idempotent():
    scheduler = Scheduler(db=FakeDB(), now_fn=now, check_interval=0.05)

    await scheduler.start()
    await scheduler.start()
    assert scheduler.running

    await scheduler.stop()
    await scheduler.stop()
    assert not scheduler.running
