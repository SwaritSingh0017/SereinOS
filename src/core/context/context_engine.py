"""
Context Engine - Provides awareness of the current situation.
Time, date, open applications, internet status, user activity, environmental context.
"""
from src.core.base import BaseEngine, ContextState, event_bus
from src.database.database import Database
from datetime import datetime
from typing import Dict, List, Optional, Any
import json
import platform
import psutil


class ContextEngine(BaseEngine):
    """Manages environmental and situational context awareness."""
    
    @property
    def engine_name(self) -> str:
        return "context"
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._context: Optional[ContextState] = None
        self._session_start: datetime = datetime.now()
        self._user_activity_history: List[Dict] = []
    
    def initialize(self) -> None:
        """Initialize context awareness."""
        if not self.db:
            self.db = Database()
        
        self._context = ContextState()
        self._session_start = datetime.now()
        self._update_context()
        
        self._initialized = True
        event_bus.emit("context.initialized", {"state": self._context.to_dict()})
    
    def _update_context(self) -> None:
        """Updates all context fields."""
        if not self._context:
            return
        
        now = datetime.now()
        
        # Time and date
        self._context.current_time = now.strftime("%H:%M")
        self._context.date = now.strftime("%Y-%m-%d")
        self._context.day_of_week = now.strftime("%A")
        
        # Session duration
        self._context.session_duration_minutes = int(
            (now - self._session_start).total_seconds() / 60
        )
        
        # Active applications (simplified - in reality would use OS APIs)
        self._context.active_applications = self._get_active_applications()
        
        # Internet connectivity
        self._context.internet_connected = self._check_internet()
        
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
    
    def _check_internet(self) -> bool:
        """Checks internet connectivity."""
        try:
            import socket
            socket.create_connection(("8.8.8.8", 53), timeout=2)
            return True
        except OSError:
            return False
    
    def save_state(self) -> None:
        """Context is ephemeral, not saved to database."""
        pass
    
    def get_state(self) -> ContextState:
        return self._context or ContextState()
    
    def get_context(self) -> ContextState:
        """Returns current context (refreshes first)."""
        self._update_context()
        return self._context or ContextState()
    
    def get_context_context(self) -> str:
        """Returns formatted context for LLM prompts."""
        ctx = self.get_context()
        
        apps_str = ", ".join(ctx.active_applications[:5]) if ctx.active_applications else "None detected"
        if len(ctx.active_applications) > 5:
            apps_str += f" (+{len(ctx.active_applications) - 5} more)"
        
        return f"""CURRENT CONTEXT:
- Time: {ctx.current_time} ({ctx.day_of_week}, {ctx.date})
- Session Duration: {ctx.session_duration_minutes} minutes
- Active Applications: {apps_str}
- Internet: {'Connected' if ctx.internet_connected else 'Offline'}
- Platform: {ctx.location_context}
- User Activity: {ctx.user_activity}"""
    
    def set_user_activity(self, activity: str) -> None:
        """Sets the current user activity."""
        if self._context:
            old_activity = self._context.user_activity
            self._context.user_activity = activity
            self._context.updated_at = datetime.now()
            
            # Record activity change
            self._user_activity_history.append({
                "timestamp": datetime.now().isoformat(),
                "from": old_activity,
                "to": activity
            })
            self._user_activity_history = self._user_activity_history[-50:]
            
            event_bus.emit("context.activity_changed", {
                "from": old_activity,
                "to": activity
            })
    
    def record_user_action(self, action: str, details: Dict = None) -> None:
        """Records a user action for context awareness."""
        if not self._context:
            return
        
        self._user_activity_history.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details or {}
        })
        self._user_activity_history = self._user_activity_history[-100:]
        
        # Update activity based on action
        activity_map = {
            "coding": "coding",
            "writing": "writing",
            "reading": "reading",
            "gaming": "gaming",
            "meeting": "in_meeting",
            "break": "on_break",
        }
        
        for keyword, act in activity_map.items():
            if keyword in action.lower():
                self.set_user_activity(act)
                break
    
    def get_time_context(self) -> Dict[str, Any]:
        """Returns detailed time-based context."""
        now = datetime.now()
        hour = now.hour
        
        time_of_day = "night"
        if 5 <= hour < 12:
            time_of_day = "morning"
        elif 12 <= hour < 17:
            time_of_day = "afternoon"
        elif 17 <= hour < 22:
            time_of_day = "evening"
        
        return {
            "hour": hour,
            "time_of_day": time_of_day,
            "day_of_week": now.strftime("%A"),
            "is_weekend": now.weekday() >= 5,
            "is_late_night": hour >= 22 or hour < 5,
            "is_work_hours": 9 <= hour < 17 and now.weekday() < 5,
        }
    
    def get_system_context(self) -> Dict[str, Any]:
        """Returns system resource context."""
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_available_gb": memory.available / (1024**3),
                "disk_percent": disk.percent,
                "disk_free_gb": disk.free / (1024**3),
            }
        except Exception:
            return {}
    
    def should_suggest_break(self) -> bool:
        """Determines if a break should be suggested."""
        ctx = self.get_context()
        time_ctx = self.get_time_context()
        sys_ctx = self.get_system_context()
        
        # Long session
        if ctx.session_duration_minutes > 90:
            return True
        
        # Late night
        if time_ctx.get("is_late_night"):
            return True
        
        # High resource usage (user might be stressed)
        if sys_ctx.get("cpu_percent", 0) > 90:
            return True
        
        return False
    
    def get_contextual_suggestions(self) -> List[str]:
        """Returns context-aware proactive suggestions."""
        suggestions = []
        ctx = self.get_context()
        time_ctx = self.get_time_context()
        
        if self.should_suggest_break():
            suggestions.append("You've been at it for a while. Want to take a short break?")
        
        if time_ctx.get("is_late_night") and ctx.session_duration_minutes > 60:
            suggestions.append("It's getting late. Should we wrap up for the night?")
        
        if time_ctx.get("is_weekend") and time_ctx.get("time_of_day") == "morning":
            suggestions.append("Good morning! Any plans for the weekend?")
        
        if ctx.user_activity == "coding" and ctx.session_duration_minutes > 45:
            suggestions.append("Been coding for a bit. Want me to review anything or help with a break?")
        
        return suggestions
    
    def close(self) -> None:
        """Cleans up."""
        super().close()