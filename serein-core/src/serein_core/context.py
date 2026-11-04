"""
Context Engine - Provides awareness of the current situation.
Time, date, open applications, internet status, user activity, environmental context.
"""
import asyncio
import logging
import platform
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any

import psutil

from serein_core.base import BaseEngine, ContextState, event_bus
from serein_core.database import Database
from serein_core.events import CoreEvents, Event, event_bus as core_event_bus

logger = logging.getLogger("serein.core.context")


class ContextEngine(BaseEngine):
    """Manages environmental and situational context awareness."""
    
    @property
    def engine_name(self) -> str:
        return "context"
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._context: Optional[ContextState] = ContextState()
        self._session_start: datetime = datetime.now()
        self._user_activity_history: List[Dict] = []
        
        # Non-blocking network caching and transition tracking
        self._cached_internet: bool = False
        self._last_network_check: float = 0.0
        self._network_cache_ttl: float = 5.0
        self._previous_internet_state: Optional[bool] = None
        self._background_probe_task: Optional[asyncio.Task] = None
        self._running_monitor: bool = False
    
    async def initialize(self) -> None:
        """Initialize context awareness."""
        if not self.db:
            from serein_core.database import get_database
            self.db = await get_database()
        
        self._context = ContextState()
        self._session_start = datetime.now()
        
        # Perform initial non-blocking probe in threadpool
        init_online = await self.check_internet_async(timeout=0.5)
        self._set_network_state(init_online)
        self._update_context()
        
        self._running_monitor = True
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                self._background_probe_task = loop.create_task(self._background_network_monitor())
        except RuntimeError:
            pass
        
        self._initialized = True
        await event_bus.publish("context.initialized", {"state": self._context.to_dict()})
    
    async def _background_network_monitor(self) -> None:
        """Periodic background network check to keep cached state fresh without blocking."""
        while self._running_monitor:
            try:
                await asyncio.sleep(self._network_cache_ttl)
                if not self._running_monitor:
                    break
                is_online = await self.check_internet_async(timeout=0.5)
                self._set_network_state(is_online)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Background network monitor error: {e}")
    
    def _set_network_state(self, is_online: bool) -> None:
        """Update cached network state and emit transition event if state flipped."""
        old_state = self._previous_internet_state
        self._cached_internet = is_online
        self._last_network_check = time.time()
        
        if self._context:
            self._context.internet_connected = is_online
        
        if old_state is not None and old_state != is_online:
            self._emit_network_transition(is_online)
            
        self._previous_internet_state = is_online
    
    def _emit_network_transition(self, is_online: bool) -> None:
        """Emit CoreEvents.SYSTEM_STATE_CHANGED on transition."""
        network_str = "online" if is_online else "offline"
        event_data = {"network": network_str}
        logger.info(f"Network state transition detected: {network_str}")
        
        try:
            evt = Event(type=CoreEvents.SYSTEM_STATE_CHANGED, data=event_data)
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(core_event_bus.publish(evt))
            except RuntimeError:
                pass
        except Exception as e:
            logger.debug(f"Failed to publish system state changed event: {e}")
    
    async def set_connectivity(self, is_online: bool) -> None:
        """Explicitly set network state and emit transition if flipped (useful in tests/offline mode)."""
        old_state = self._previous_internet_state
        self._cached_internet = is_online
        self._last_network_check = time.time()
        if self._context:
            self._context.internet_connected = is_online
        if old_state is None or old_state != is_online:
            network_str = "online" if is_online else "offline"
            evt = Event(type=CoreEvents.SYSTEM_STATE_CHANGED, data={"network": network_str})
            await core_event_bus.publish(evt)
        self._previous_internet_state = is_online

    def _update_context(self) -> None:
        """Updates all context fields without blocking the event loop."""
        if not self._context:
            self._context = ContextState()
        
        now = datetime.now()
        
        # Time and date
        self._context.current_time = now.strftime("%H:%M")
        self._context.date = now.strftime("%Y-%m-%d")
        self._context.day_of_week = now.strftime("%A")
        
        # Session duration
        self._context.session_duration_minutes = int(
            (now - self._session_start).total_seconds() / 60
        )
        
        # Active applications
        self._context.active_applications = self._get_active_applications()
        
        # Fast non-blocking cached internet connectivity (strictly read cached state)
        self._context.internet_connected = self._cached_internet
        
        # Platform info
        self._context.location_context = f"{platform.system()} {platform.release()}"
    
    def _get_active_applications(self) -> List[str]:
        """Gets list of active applications."""
        try:
            apps = []
            for proc in psutil.process_iter(['name']):
                try:
                    name = proc.info['name']
                    if name and name not in apps:
                        apps.append(name)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            # Return top 10 non-system apps
            system_apps = {'system', 'systemd', 'kernel', 'init', 'svchost', 'explorer', 'finder'}
            filtered = [a for a in apps if a.lower() not in system_apps]
            return filtered[:10]
        except Exception:
            return []
    
    def _check_internet(self, timeout: float = 0.5) -> bool:
        """Checks internet connectivity with fast timeout (0.5s default)."""
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=timeout)
            return True
        except OSError:
            return False
    
    async def check_internet_async(self, timeout: float = 0.5) -> bool:
        """Non-blocking async probe executing in thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._check_internet, timeout)
    
    async def save_state(self) -> None:
        """Context is ephemeral, not saved to database."""
        pass
    
    def get_state(self) -> ContextState:
        return self._context or ContextState()
    
    def get_context(self) -> ContextState:
        """Returns current context (refreshes first)."""
        self._update_context()
        return self._context or ContextState()
    
    async def close(self) -> None:
        """Stop background monitor tasks."""
        self._running_monitor = False
        if self._background_probe_task and not self._background_probe_task.done():
            self._background_probe_task.cancel()
            try:
                await self._background_probe_task
            except asyncio.CancelledError:
                pass
            self._background_probe_task = None


# Global instance
_context_engine = None

async def get_context_engine() -> ContextEngine:
    """Get or create the global context engine."""
    global _context_engine
    if _context_engine is None:
        _context_engine = ContextEngine()
        await _context_engine.initialize()
    return _context_engine