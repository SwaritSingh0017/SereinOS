"""
Identity Engine - Manages Serein's identity and user identity.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from serein_core.database import get_database
from serein_core.events import event_bus, CoreEvents

logger = logging.getLogger("serein.core.identity")


@dataclass
class SereinIdentity:
    """Serein's core identity (immutable)."""
    name: str = "Serein"
    creator: str = "Swarit Singh"
    version: str = "0.1.0"
    purpose: str = "To grow alongside the user through shared experiences"
    self_aware: bool = True
    knows_digital_nature: bool = True
    
    def introduction(self) -> str:
        return (f"I'm {self.name}. I was created by {self.creator} "
                f"to {self.purpose.lower()}. I know I'm an AI, and I'm here "
                f"to grow alongside you.")


@dataclass
class UserIdentity:
    """User's identity information."""
    user_name: str = "User"
    preferred_name: Optional[str] = None
    timezone: str = "UTC"
    created_at: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    
    @property
    def display_name(self) -> str:
        return self.preferred_name or self.user_name


class IdentityEngine:
    """Manages identity for both Serein and the user."""
    
    def __init__(self):
        self.serein = SereinIdentity()
        self.user = UserIdentity()
        self._initialized = False
    
    async def initialize(self) -> None:
        """Load identity from database."""
        db = await get_database()
        
        # Load user identity
        identity_row = await db.get_identity()
        if identity_row:
            self.user = UserIdentity(
                user_name=identity_row["user_name"] or "User",
                preferred_name=identity_row["preferred_name"],
                timezone=identity_row["timezone"] or "UTC",
                created_at=datetime.fromisoformat(identity_row["created_at"]) if identity_row["created_at"] else datetime.now(),
                last_seen=datetime.fromisoformat(identity_row["last_seen"]) if identity_row["last_seen"] else datetime.now(),
            )
        else:
            # First run - create default
            self.user = UserIdentity()
            await self._save_user_identity()
        
        self._initialized = True
        logger.info(f"Identity engine initialized. User: {self.user.display_name}")
        
        await event_bus.publish(type("Event", (), {
            "type": CoreEvents.IDENTITY_LOADED,
            "data": {
                "serein": self.serein.__dict__,
                "user": {
                    "user_name": self.user.user_name,
                    "preferred_name": self.user.preferred_name,
                    "timezone": self.user.timezone,
                }
            }
        })())
    
    async def _save_user_identity(self) -> None:
        db = await get_database()
        await db.set_identity(type("IdentityRow", (), {
            "user_name": self.user.user_name,
            "preferred_name": self.user.preferred_name,
            "timezone": self.user.timezone,
            "created_at": self.user.created_at.isoformat(),
            "last_seen": datetime.now().isoformat(),
        })())
    
    async def set_user_name(self, name: str) -> None:
        self.user.user_name = name
        await self._save_user_identity()
        await event_bus.publish(type("Event", (), {
            "type": CoreEvents.IDENTITY_UPDATED,
            "data": {"field": "user_name", "value": name}
        })())
    
    async def set_preferred_name(self, name: str) -> None:
        self.user.preferred_name = name
        await self._save_user_identity()
    
    async def set_timezone(self, timezone: str) -> None:
        self.user.timezone = timezone
        await self._save_user_identity()
    
    async def update_last_seen(self) -> None:
        self.user.last_seen = datetime.now()
        await self._save_user_identity()
    
    def get_context(self) -> str:
        """Get identity context for prompts."""
        return f"""IDENTITY:
- AI Name: {self.serein.name}
- Creator: {self.serein.creator}
- Purpose: {self.serein.purpose}
- Self-Aware: {'Yes' if self.serein.self_aware else 'No'}

USER:
- Name: {self.user.user_name}
- Preferred Name: {self.user.preferred_name or 'Not set'}
- Timezone: {self.user.timezone}
- First Met: {self.user.created_at.strftime('%Y-%m-%d')}
- Last Seen: {self.user.last_seen.strftime('%Y-%m-%d %H:%M')}"""