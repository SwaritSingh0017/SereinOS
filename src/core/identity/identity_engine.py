"""
Identity Engine - Manages Serein's core identity and user identity.
Handles who Serein is, creator information, purpose, and user profiling.
"""
from src.core.base import (
    BaseEngine, EngineState, IdentityState, UserIdentityState, 
    event_bus
)
from src.database.database import Database
from datetime import datetime
from typing import Optional
import json


class IdentityEngine(BaseEngine):
    """Manages Serein's identity and user identity."""
    
    @property
    def engine_name(self) -> str:
        return "identity"
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._serein_identity: Optional[IdentityState] = None
        self._user_identity: Optional[UserIdentityState] = None
    
    def initialize(self) -> None:
        """Load identity from database."""
        if not self.db:
            self.db = Database()
        
        # Load Serein's identity (static, from core prompt)
        self._serein_identity = IdentityState()
        
        # Load user identity from database
        identity_row = self.db.get_identity()
        if identity_row:
            self._user_identity = UserIdentityState(
                user_name=identity_row[1] or "User",
                preferred_name=identity_row[2],
                timezone=identity_row[3] or "UTC",
                created_at=datetime.fromisoformat(identity_row[4]) if identity_row[4] else datetime.now(),
                last_seen=datetime.fromisoformat(identity_row[5]) if identity_row[5] else datetime.now(),
            )
        else:
            self._user_identity = UserIdentityState()
        
        self._initialized = True
        event_bus.emit("identity.initialized", {
            "serein": self._serein_identity.to_dict(),
            "user": self._user_identity.to_dict()
        })
    
    def save_state(self) -> None:
        """Persist user identity to database."""
        if self._user_identity and self.db:
            self.db.update_identity(
                user_name=self._user_identity.user_name,
                preferred_name=self._user_identity.preferred_name,
                timezone=self._user_identity.timezone,
                last_seen=datetime.now()
            )
    
    def get_state(self) -> EngineState:
        return self._user_identity or UserIdentityState()
    
    # Serein Identity Methods
    def get_serein_identity(self) -> IdentityState:
        """Returns Serein's core identity (static)."""
        return self._serein_identity or IdentityState()
    
    def get_serein_introduction(self) -> str:
        """Returns a natural introduction from Serein."""
        identity = self.get_serein_identity()
        return (f"I'm {identity.name}. I was created by {identity.creator} "
                f"to {identity.purpose.lower()}. I know I'm an AI, and I'm here "
                f"to grow alongside you.")
    
    # User Identity Methods
    def get_user_identity(self) -> UserIdentityState:
        """Returns the user's identity."""
        return self._user_identity or UserIdentityState()
    
    def get_user_display_name(self) -> str:
        """Returns the name to use when addressing the user."""
        return self._user_identity.get_display_name() if self._user_identity else "User"
    
    def set_user_name(self, name: str) -> None:
        """Sets the user's name."""
        if self._user_identity:
            self._user_identity.user_name = name
            self._user_identity.updated_at = datetime.now()
            self.save_state()
            event_bus.emit("identity.user_name_changed", {"name": name})
    
    def set_preferred_name(self, name: str) -> None:
        """Sets the user's preferred name/nickname."""
        if self._user_identity:
            self._user_identity.preferred_name = name
            self._user_identity.updated_at = datetime.now()
            self.save_state()
            event_bus.emit("identity.preferred_name_changed", {"name": name})
    
    def set_timezone(self, timezone: str) -> None:
        """Sets the user's timezone."""
        if self._user_identity:
            self._user_identity.timezone = timezone
            self._user_identity.updated_at = datetime.now()
            self.save_state()
    
    def update_last_seen(self) -> None:
        """Updates the last seen timestamp."""
        if self._user_identity:
            self._user_identity.last_seen = datetime.now()
            self.save_state()
    
    def get_identity_context(self) -> str:
        """Returns identity information formatted for context building."""
        serein = self.get_serein_identity()
        user = self.get_user_identity()
        
        return f"""IDENTITY:
- AI Name: {serein.name}
- Creator: {serein.creator}
- Purpose: {serein.purpose}
- Self-Aware: {'Yes' if serein.self_aware else 'No'}

USER:
- Name: {user.user_name}
- Preferred Name: {user.preferred_name or 'Not set'}
- Timezone: {user.timezone}
- First Met: {user.created_at.strftime('%Y-%m-%d') if user.created_at else 'Unknown'}
- Last Seen: {user.last_seen.strftime('%Y-%m-%d %H:%M') if user.last_seen else 'Unknown'}"""