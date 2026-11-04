"""
Scheduler - Always-on background service for reminders, recurring tasks, and conditional triggers.
Part of the split-process architecture ensuring reminders fire even if avatar UI is closed.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set
from uuid import uuid4
import croniter
import json

from serein_core.database import get_database
from serein_core.events import event_bus, CoreEvents
from serein_core.command_bus import get_command_bus

logger = logging.getLogger("serein.core.scheduler")


class TriggerType(Enum):
    ABSOLUTE = "absolute"        # Fire once at specific time
    RELATIVE = "relative"        # Fire after delay (e.g., "in 20 minutes")
    RECURRING = "recurring"      # Fire on cron schedule
    CONDITIONAL = "conditional"  # Fire when condition met


class EventStatus(str, Enum):
    PENDING = "pending"
    FIRED = "fired"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class ScheduledEvent:
    id: str
    trigger_type: TriggerType
    fires_at: Optional[datetime] = None
    recurrence_rule: Optional[str] = None  # cron expression
    condition_spec: Dict = field(default_factory=dict)  # For conditional triggers
    message: str = ""
    status: EventStatus = EventStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    fired_at: Optional[datetime] = None
    payload: Dict = field(default_factory=dict)  # Additional data for execution

    def to_dict(self) -> Dict:
        status_val = self.status.value if isinstance(self.status, EventStatus) else str(self.status)
        return {
            "id": self.id,
            "trigger_type": self.trigger_type.value,
            "fires_at": self.fires_at.isoformat() if self.fires_at else None,
            "recurrence_rule": self.recurrence_rule,
            "condition_spec": self.condition_spec,
            "message": self.message,
            "status": status_val,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "fired_at": self.fired_at.isoformat() if self.fired_at else None,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ScheduledEvent":
        st = data.get("status", "pending")
        if isinstance(st, str):
            try:
                st = EventStatus(st)
            except ValueError:
                st = EventStatus.PENDING

        payload = data.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}

        cond = data.get("condition_spec", {})
        if isinstance(cond, str):
            try:
                cond = json.loads(cond)
            except Exception:
                cond = {}

        event = cls(
            id=data["id"],
            trigger_type=TriggerType(data["trigger_type"]),
            message=data.get("message", ""),
            status=st,
            condition_spec=cond,
            payload=payload,
        )
        if data.get("fires_at"):
            event.fires_at = datetime.fromisoformat(data["fires_at"])
        if data.get("recurrence_rule"):
            event.recurrence_rule = data["recurrence_rule"]
        if data.get("created_at"):
            event.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("fired_at"):
            event.fired_at = datetime.fromisoformat(data["fired_at"])
        return event


class ConditionEvaluator:
    """Evaluates conditional triggers."""

    def __init__(self):
        self._conditions: Dict[str, Callable[[Dict], Awaitable[bool]]] = {}

    def register_condition(self, name: str, evaluator: Callable[[Dict], Awaitable[bool]]) -> None:
        """Register a condition evaluator."""
        self._conditions[name] = evaluator

    async def evaluate(self, condition_spec: Dict) -> bool:
        """Evaluate a condition specification."""
        if not condition_spec:
            return True

        cond_type = condition_spec.get("type")
        if cond_type == "always":
            return True

        if cond_type == "all":
            for sub_cond in condition_spec.get("conditions", []):
                if not await self.evaluate(sub_cond):
                    return False
            return True

        elif cond_type == "any":
            for sub_cond in condition_spec.get("conditions", []):
                if await self.evaluate(sub_cond):
                    return True
            return False

        elif cond_type == "not":
            return not await self.evaluate(condition_spec.get("condition", {}))

        elif cond_type in self._conditions:
            try:
                res = self._conditions[cond_type](condition_spec)
                if asyncio.iscoroutine(res):
                    return await res
                return bool(res)
            except Exception as e:
                logger.error(f"Condition evaluation error for {cond_type}: {e}")
                return False

        return False


class Scheduler:
    """
    Always-on scheduler for reminders, recurring tasks, and conditional triggers.
    Runs as part of the core service (not the avatar process).
    """

    def __init__(
        self,
        db=None,
        event_bus_instance=None,
        command_bus=None,
        now_fn: Optional[Callable[[], datetime]] = None,
        check_interval: float = 10.0,
    ):
        self.db = db
        self.event_bus = event_bus_instance or event_bus
        self.command_bus = command_bus
        self._now_fn = now_fn or datetime.now
        self._events: Dict[str, ScheduledEvent] = {}
        self._running = False
        self._check_interval = check_interval
        self._task: Optional[asyncio.Task] = None
        self._condition_evaluator = ConditionEvaluator()
        self._register_builtin_conditions()
        self._next_fire: Dict[str, datetime] = {}

    @property
    def running(self) -> bool:
        return self._running

    def _now(self) -> datetime:
        return self._now_fn()

    def _register_builtin_conditions(self) -> None:
        """Register built-in condition evaluators."""

        async def battery_low(spec: Dict) -> bool:
            threshold = spec.get("threshold", 20)
            try:
                import psutil
                battery = psutil.sensors_battery()
                return battery is not None and battery.percent <= threshold and not battery.power_plugged
            except Exception:
                return False

        async def time_after(spec: Dict) -> bool:
            time_str = spec.get("time", "22:00")
            try:
                target = datetime.strptime(time_str, "%H:%M").time()
                now = self._now().time()
                return now >= target
            except Exception:
                return False

        async def time_before(spec: Dict) -> bool:
            time_str = spec.get("time", "08:00")
            try:
                target = datetime.strptime(time_str, "%H:%M").time()
                now = self._now().time()
                return now <= target
            except Exception:
                return False

        async def cpu_high(spec: Dict) -> bool:
            threshold = spec.get("threshold", 90)
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=0.1)
                return cpu >= threshold
            except Exception:
                return False

        async def memory_high(spec: Dict) -> bool:
            threshold = spec.get("threshold", 90)
            try:
                import psutil
                mem = psutil.virtual_memory()
                return mem.percent >= threshold
            except Exception:
                return False

        async def idle_time(spec: Dict) -> bool:
            return False

        async def app_running(spec: Dict) -> bool:
            app_name = spec.get("app", "")
            try:
                import psutil
                for proc in psutil.process_iter(['name']):
                    if proc.info['name'] and app_name.lower() in proc.info['name'].lower():
                        return True
                return False
            except Exception:
                return False

        self._condition_evaluator.register_condition("always", lambda _: True)
        self._condition_evaluator.register_condition("battery_low", battery_low)
        self._condition_evaluator.register_condition("time_after", time_after)
        self._condition_evaluator.register_condition("time_before", time_before)
        self._condition_evaluator.register_condition("cpu_high", cpu_high)
        self._condition_evaluator.register_condition("memory_high", memory_high)
        self._condition_evaluator.register_condition("idle_time", idle_time)
        self._condition_evaluator.register_condition("app_running", app_running)

    async def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            return

        self._running = True
        await self._load_events()
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("Scheduler started")

        await self.event_bus.publish(type("Event", (), {
            "type": CoreEvents.SCHEDULER_STARTED,
            "data": {"events_loaded": len(self._events)}
        })())

    async def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Scheduler stopped")

    async def _load_events(self) -> None:
        """Load pending events from database."""
        db = self.db
        if db is None:
            try:
                db = await get_database()
            except Exception:
                db = None

        if db is not None:
            if hasattr(db, "fetch_all"):
                events_data = await db.fetch_all("SELECT * FROM scheduled_events WHERE status = 'pending'")
            elif hasattr(db, "get_pending_events"):
                events_data = await db.get_pending_events(self._now() + timedelta(days=365))
            else:
                events_data = []

            for data in events_data:
                if isinstance(data, dict):
                    event = ScheduledEvent.from_dict(data)
                    self._events[event.id] = event

                    if event.trigger_type == TriggerType.RECURRING and event.recurrence_rule:
                        try:
                            cron = croniter.croniter(event.recurrence_rule, self._now())
                            self._next_fire[event.id] = cron.get_next(datetime)
                        except Exception as e:
                            logger.warning(f"Invalid cron expression for {event.id}: {e}")
                    elif event.fires_at:
                        self._next_fire[event.id] = event.fires_at

        logger.info(f"Loaded {len(self._events)} scheduled events")

    async def schedule_event(self, event: ScheduledEvent) -> str:
        """Schedule a new event."""
        db = self.db
        if db is None:
            try:
                db = await get_database()
            except Exception:
                db = None

        status_val = event.status.value if isinstance(event.status, EventStatus) else str(event.status)
        if db is not None:
            if hasattr(db, "execute"):
                await db.execute(
                    "INSERT INTO scheduled_events (id, trigger_type, fires_at, recurrence_rule, condition_spec, message, status, created_at, fired_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.id,
                        event.trigger_type.value,
                        event.fires_at.isoformat() if event.fires_at else None,
                        event.recurrence_rule,
                        json.dumps(event.condition_spec),
                        event.message,
                        status_val,
                        event.created_at.isoformat() if event.created_at else None,
                        event.fired_at.isoformat() if event.fired_at else None,
                        json.dumps(event.payload),
                    )
                )
            elif hasattr(db, "save_scheduled_event"):
                await db.save_scheduled_event(event.to_dict())

        self._events[event.id] = event

        if event.trigger_type == TriggerType.RECURRING and event.recurrence_rule:
            try:
                cron = croniter.croniter(event.recurrence_rule, self._now())
                self._next_fire[event.id] = cron.get_next(datetime)
            except Exception as e:
                logger.warning(f"Invalid cron expression for {event.id}: {e}")
        elif event.fires_at:
            self._next_fire[event.id] = event.fires_at

        await self.event_bus.publish(type("Event", (), {
            "type": CoreEvents.EVENT_SCHEDULED,
            "data": event.to_dict()
        })())

        return event.id

    async def cancel_event(self, event_id: str) -> bool:
        """Cancel a scheduled event."""
        if event_id in self._events:
            self._events[event_id].status = EventStatus.CANCELLED
            self._next_fire.pop(event_id, None)

            db = self.db
            if db is None:
                try:
                    db = await get_database()
                except Exception:
                    db = None
            if db is not None and hasattr(db, "execute"):
                await db.execute(
                    "UPDATE scheduled_events SET status = 'cancelled' WHERE id = ?",
                    (event_id,)
                )
            return True
        return False

    async def get_event(self, event_id: str) -> Optional[ScheduledEvent]:
        """Get scheduled event by id."""
        return self._events.get(event_id)

    async def get_pending_events(self) -> List[ScheduledEvent]:
        """Get all pending events."""
        return [e for e in self._events.values() if e.status == EventStatus.PENDING]

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop - checks and fires events."""
        while self._running:
            try:
                now = self._now()
                await self._check_events(now)
                await self._check_conditions(now)
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")

            await asyncio.sleep(self._check_interval)

    async def _check_events(self, now: datetime) -> None:
        """Check for events that should fire."""
        to_fire = []

        for event_id, event in list(self._events.items()):
            if event.status != EventStatus.PENDING:
                continue

            should_fire = False

            if event.trigger_type in (TriggerType.ABSOLUTE, TriggerType.RELATIVE):
                if event.fires_at and event.fires_at <= now:
                    should_fire = True

            elif event.trigger_type == TriggerType.RECURRING:
                next_fire = self._next_fire.get(event_id)
                if next_fire and next_fire <= now:
                    should_fire = True

            if should_fire:
                to_fire.append(event)

        for event in to_fire:
            await self._fire_event(event)

    async def _check_conditions(self, now: Optional[datetime] = None) -> None:
        """Check conditional triggers."""
        for event_id, event in list(self._events.items()):
            if event.status != EventStatus.PENDING or event.trigger_type != TriggerType.CONDITIONAL:
                continue

            if event.condition_spec:
                try:
                    if await self._condition_evaluator.evaluate(event.condition_spec):
                        await self._fire_event(event)
                except Exception as e:
                    logger.error(f"Condition evaluation error for {event_id}: {e}")

    async def _fire_event(self, event: ScheduledEvent) -> None:
        """Fire a scheduled event."""
        logger.info(f"Firing event: {event.id} - {event.message}")

        now_time = self._now()
        try:
            event.status = EventStatus.FIRED
            event.fired_at = now_time

            db = self.db
            if db is None:
                try:
                    db = await get_database()
                except Exception:
                    db = None

            if db is not None:
                if hasattr(db, "mark_event_fired"):
                    await db.mark_event_fired(event.id)
                elif hasattr(db, "execute"):
                    await db.execute(
                        "UPDATE scheduled_events SET status = 'fired', fired_at = ? WHERE id = ?",
                        (event.fired_at.isoformat(), event.id)
                    )

            if event.trigger_type == TriggerType.RECURRING and event.recurrence_rule:
                try:
                    cron = croniter.croniter(event.recurrence_rule, event.fired_at or now_time)
                    self._next_fire[event.id] = cron.get_next(datetime)
                    event.status = EventStatus.PENDING
                    if db is not None and hasattr(db, "execute"):
                        await db.execute(
                            "UPDATE scheduled_events SET status = 'pending', fired_at = ? WHERE id = ?",
                            (event.fired_at.isoformat(), event.id)
                        )
                except Exception as e:
                    logger.error(f"Failed to calculate next fire for {event.id}: {e}")

            await self._execute_event_action(event)

            await self.event_bus.publish(type("Event", (), {
                "type": "core.scheduler.event_fired",
                "data": event.to_dict()
            })())

        except Exception as e:
            logger.error(f"Failed to fire event {event.id}: {e}")
            event.status = EventStatus.FAILED

    async def _execute_event_action(self, event: ScheduledEvent) -> None:
        """Execute the action associated with a fired event."""
        bus = self.command_bus
        if bus is None:
            try:
                bus = await get_command_bus()
            except Exception:
                bus = None

        if event.payload:
            action = event.payload.get("action")
            if action and bus:
                try:
                    await bus.execute(action, event.payload.get("params", {}))
                except Exception as e:
                    logger.error(f"Failed to execute payload action: {e}")

        elif event.message and bus:
            try:
                await bus.execute("send_notification", {
                    "title": "Serein Reminder",
                    "body": event.message,
                    "urgency": "normal",
                })
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")

    # Convenience methods for common scheduling

    async def remind_at(self, message: str, when: datetime, action: Optional[str] = None, params: Optional[Dict] = None, **kwargs) -> str:
        """Schedule a one-time reminder at specific time."""
        payload = dict(kwargs)
        if action:
            payload["action"] = action
        if params:
            payload["params"] = params
        event = ScheduledEvent(
            id=str(uuid4()),
            trigger_type=TriggerType.ABSOLUTE,
            fires_at=when,
            message=message,
            status=EventStatus.PENDING,
            created_at=self._now(),
            payload=payload,
        )
        return await self.schedule_event(event)

    async def remind_in(self, message: str, delay: timedelta, action: Optional[str] = None, params: Optional[Dict] = None, **kwargs) -> str:
        """Schedule a reminder after a delay."""
        if delay.total_seconds() <= 0:
            raise ValueError("Delay must be positive")
        payload = dict(kwargs)
        if action:
            payload["action"] = action
        if params:
            payload["params"] = params
        event = ScheduledEvent(
            id=str(uuid4()),
            trigger_type=TriggerType.RELATIVE,
            fires_at=self._now() + delay,
            message=message,
            status=EventStatus.PENDING,
            created_at=self._now(),
            payload=payload,
        )
        return await self.schedule_event(event)

    async def remind_recurring(self, message: str, cron_expr: str, action: Optional[str] = None, params: Optional[Dict] = None, **kwargs) -> str:
        """Schedule a recurring reminder."""
        try:
            croniter.croniter(cron_expr, self._now())
        except Exception as e:
            raise ValueError(f"Invalid cron expression: {e}")

        payload = dict(kwargs)
        if action:
            payload["action"] = action
        if params:
            payload["params"] = params
        event = ScheduledEvent(
            id=str(uuid4()),
            trigger_type=TriggerType.RECURRING,
            recurrence_rule=cron_expr,
            message=message,
            status=EventStatus.PENDING,
            created_at=self._now(),
            payload=payload,
        )
        return await self.schedule_event(event)

    async def remind_when(self, message: str, condition_spec: Dict, action: Optional[str] = None, params: Optional[Dict] = None, **kwargs) -> str:
        """Schedule a conditional reminder."""
        payload = dict(kwargs)
        if action:
            payload["action"] = action
        if params:
            payload["params"] = params
        event = ScheduledEvent(
            id=str(uuid4()),
            trigger_type=TriggerType.CONDITIONAL,
            condition_spec=condition_spec,
            message=message,
            status=EventStatus.PENDING,
            created_at=self._now(),
            payload=payload,
        )
        return await self.schedule_event(event)

    async def get_status(self) -> Dict[str, Any]:
        """Get scheduler status."""
        return {
            "running": self._running,
            "total_events": len(self._events),
            "pending_events": len([e for e in self._events.values() if e.status == EventStatus.PENDING]),
            "next_fires": {
                k: v.isoformat() for k, v in self._next_fire.items()
            },
        }


# Global scheduler instance
scheduler: Optional[Scheduler] = None


async def get_scheduler() -> Scheduler:
    global scheduler
    if scheduler is None:
        scheduler = Scheduler()
        await scheduler.start()
    return scheduler
