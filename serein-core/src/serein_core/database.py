"""
Database layer with SQLCipher encryption support.
Provides async interface for all core data operations.
"""
import asyncio
import sqlite3
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncIterator
from uuid import uuid4

import os
try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    sqlcipher = None
from pydantic import BaseModel

logger = logging.getLogger("serein.core.database")


SCHEMA = """
-- Core identity
CREATE TABLE IF NOT EXISTS identity (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    user_name TEXT NOT NULL,
    preferred_name TEXT,
    timezone TEXT DEFAULT 'UTC',
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

-- Personality traits (stable)
CREATE TABLE IF NOT EXISTS personality_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    calm INTEGER DEFAULT 90,
    mature INTEGER DEFAULT 85,
    cheerful INTEGER DEFAULT 70,
    energetic INTEGER DEFAULT 60,
    protective INTEGER DEFAULT 80,
    companion INTEGER DEFAULT 85,
    professional INTEGER DEFAULT 90,
    updated_at TEXT NOT NULL
);

-- Emotional state (dynamic)
CREATE TABLE IF NOT EXISTS emotion_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    happiness INTEGER DEFAULT 50,
    curiosity INTEGER DEFAULT 70,
    care INTEGER DEFAULT 60,
    concern INTEGER DEFAULT 20,
    pride INTEGER DEFAULT 10,
    affection INTEGER DEFAULT 0,
    loneliness INTEGER DEFAULT 0,
    sadness INTEGER DEFAULT 0,
    jealousy INTEGER DEFAULT 0,
    possessiveness INTEGER DEFAULT 0,
    excitement INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Relationship state
CREATE TABLE IF NOT EXISTS relationship_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    trust INTEGER DEFAULT 0,
    familiarity INTEGER DEFAULT 0,
    attachment INTEGER DEFAULT 0,
    interaction_count INTEGER DEFAULT 0,
    total_conversation_minutes INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Growth/interest adaptation
CREATE TABLE IF NOT EXISTS growth_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    technical_interest INTEGER DEFAULT 50,
    study_interest INTEGER DEFAULT 50,
    productivity_interest INTEGER DEFAULT 50,
    gaming_interest INTEGER DEFAULT 50,
    social_interest INTEGER DEFAULT 50,
    updated_at TEXT NOT NULL
);

-- Memories (semantic, episodic, emotional, working)
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    importance INTEGER DEFAULT 50,
    emotion TEXT DEFAULT 'neutral',
    source TEXT DEFAULT 'conversation',
    memory_type TEXT DEFAULT 'semantic',
    embedding BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Episodes (significant life events)
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    emotional_impact INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Goals
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    priority INTEGER DEFAULT 50,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- Tasks (actionable items)
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    sandbox_path TEXT,
    requires_confirmation BOOLEAN DEFAULT 1,
    started_at TEXT,
    completed_at TEXT,
    result_summary TEXT,
    created_at TEXT NOT NULL
);

-- Scheduled events (reminders, recurring tasks)
CREATE TABLE IF NOT EXISTS scheduled_events (
    id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL,  -- absolute, relative, recurring, conditional
    fires_at TEXT,
    recurrence_rule TEXT,        -- cron expression or RRULE
    condition_spec TEXT,         -- JSON for conditional triggers
    message TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    fired_at TEXT
);

-- System actions audit log
CREATE TABLE IF NOT EXISTS system_actions_log (
    id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    target TEXT,
    backend TEXT,
    initiated_by TEXT,           -- voice, scheduled, proactive, user
    permission_tier TEXT,
    result TEXT,
    error_message TEXT,
    timestamp TEXT NOT NULL
);

-- Permissions
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    camera_access BOOLEAN DEFAULT 0,
    microphone_access BOOLEAN DEFAULT 0,
    file_access BOOLEAN DEFAULT 1,
    internet_access BOOLEAN DEFAULT 1,
    automation_access BOOLEAN DEFAULT 0,
    system_control_access BOOLEAN DEFAULT 0,
    proactive_speech_enabled BOOLEAN DEFAULT 1,
    updated_at TEXT NOT NULL
);

-- System events
CREATE TABLE IF NOT EXISTS system_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    description TEXT,
    timestamp TEXT NOT NULL
);

-- Multi-turn dialogue history
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,          -- 'user', 'assistant', 'system'
    content TEXT NOT NULL,
    intent TEXT,
    action_taken TEXT,
    provider TEXT,
    model TEXT,
    latency_ms INTEGER,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES conversation_sessions(id)
);

-- User Preferences
CREATE TABLE IF NOT EXISTS user_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories(tags);
CREATE INDEX IF NOT EXISTS idx_scheduled_fires_at ON scheduled_events(fires_at);
CREATE INDEX IF NOT EXISTS idx_actions_timestamp ON system_actions_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON system_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON conversation_messages(session_id, timestamp ASC);
"""


@dataclass
class IdentityRow:
    user_name: str
    preferred_name: Optional[str] = None
    timezone: str = "UTC"
    created_at: str = ""
    last_seen: str = ""
    id: Optional[int] = 1

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class Database:
    """Async SQLCipher database wrapper."""
    
    def __init__(self, path: Optional[Path] = None, encryption_key: Optional[str] = None, pragmas: Dict[str, str] = None):
        if path is None:
            from serein_core.config import load_config
            cfg = load_config()
            self.path = cfg.database.path
            self.encryption_key = encryption_key or cfg.database.encryption_key
            self.pragmas = pragmas or cfg.database.pragmas
        else:
            self.path = path
            self.encryption_key = encryption_key
            self.pragmas = pragmas or {}
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
    
    async def connect(self) -> None:
        """Initialize database connection and run migrations."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Harden file permissions on POSIX systems (owner read/write only)
        if os.name == 'posix':
            try:
                os.chmod(self.path.parent, 0o700)
                if self.path.exists():
                    os.chmod(self.path, 0o600)
            except Exception:
                pass
        
        # Use thread pool for blocking DB operations
        loop = asyncio.get_event_loop()
        self._conn = await loop.run_in_executor(None, self._create_connection)
        
        await self._run_migrations()
        logger.info(f"Database connected: {self.path}")
    
    def _create_connection(self) -> sqlite3.Connection:
        if self.encryption_key and sqlcipher is not None:
            # Prevent SQL injection in PRAGMA key
            if any(c in self.encryption_key for c in ("'", ";", "\0", "\n", "\r", "--")):
                raise ValueError("Invalid characters in encryption_key: potential SQL injection detected")
            escaped_key = self.encryption_key.replace("'", "''")
            conn = sqlcipher.connect(str(self.path), check_same_thread=False)
            conn.isolation_level = None
            conn.execute(f"PRAGMA key = '{escaped_key}'")
            conn.execute("PRAGMA cipher_page_size = 4096")
            conn.execute("PRAGMA kdf_iter = 256000")
            conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
            conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")
        else:
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.isolation_level = None
        
        conn.row_factory = sqlite3.Row
        # Apply pragmas
        for pragma, value in self.pragmas.items():
            conn.execute(f"PRAGMA {pragma} = {value}")
        
        return conn
    
    async def _run_migrations(self) -> None:
        """Run schema migrations."""
        await self.execute_script(SCHEMA)
    
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[sqlite3.Connection]:
        """Context manager for transactions."""
        async with self._lock:
            try:
                self._conn.execute("BEGIN")
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
    
    async def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a query."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: self._conn.execute(query, params))
    
    async def execute_script(self, script: str) -> None:
        """Execute multiple statements."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._conn.executescript(script))
    
    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Fetch single row."""
        cursor = await self.execute(query, params)
        return cursor.fetchone()
    
    async def fetch_all(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Fetch all rows."""
        cursor = await self.execute(query, params)
        return cursor.fetchall()
    
    # Identity operations
    async def get_identity(self) -> Optional[IdentityRow]:
        row = await self.fetch_one("SELECT * FROM identity WHERE id = 1")
        return IdentityRow(**dict(row)) if row else None
    
    async def set_identity(self, identity: IdentityRow) -> None:
        now = datetime.now().isoformat()
        await self.execute("""
            INSERT INTO identity (id, user_name, preferred_name, timezone, created_at, last_seen)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_name = excluded.user_name,
                preferred_name = excluded.preferred_name,
                timezone = excluded.timezone,
                last_seen = excluded.last_seen
        """, (identity.user_name, identity.preferred_name, identity.timezone, 
              identity.created_at or now, now))
    
    async def update_last_seen(self) -> None:
        await self.execute("UPDATE identity SET last_seen = ? WHERE id = 1", 
                          (datetime.now().isoformat(),))
    
    # Personality
    async def get_personality(self) -> Dict[str, int]:
        row = await self.fetch_one("SELECT * FROM personality_state WHERE id = 1")
        if row:
            return {k: row[k] for k in row.keys() if k not in ('id', 'updated_at')}
        return {}
    
    async def update_personality(self, **traits) -> None:
        traits['updated_at'] = datetime.now().isoformat()
        cols = ', '.join(f"{k} = ?" for k in traits)
        await self.execute(f"UPDATE personality_state SET {cols} WHERE id = 1", tuple(traits.values()))
    
    # Emotions
    async def get_emotions(self) -> Dict[str, int]:
        row = await self.fetch_one("SELECT * FROM emotion_state WHERE id = 1")
        if row:
            return {k: row[k] for k in row.keys() if k not in ('id', 'updated_at')}
        return {}
    
    async def update_emotions(self, **emotions) -> None:
        emotions['updated_at'] = datetime.now().isoformat()
        cols = ', '.join(f"{k} = ?" for k in emotions)
        await self.execute(f"UPDATE emotion_state SET {cols} WHERE id = 1", tuple(emotions.values()))
    
    # Relationship
    async def get_relationship(self) -> Dict[str, Any]:
        row = await self.fetch_one("SELECT * FROM relationship_state WHERE id = 1")
        if row:
            return {k: row[k] for k in row.keys() if k not in ('id', 'updated_at')}
        return {}
    
    async def update_relationship(self, **fields) -> None:
        fields['updated_at'] = datetime.now().isoformat()
        cols = ', '.join(f"{k} = ?" for k in fields)
        await self.execute(f"UPDATE relationship_state SET {cols} WHERE id = 1", tuple(fields.values()))
    
    # Growth
    async def get_growth(self) -> Dict[str, int]:
        row = await self.fetch_one("SELECT * FROM growth_state WHERE id = 1")
        if row:
            return {k: row[k] for k in row.keys() if k not in ('id', 'updated_at')}
        return {}
    
    async def update_growth(self, **fields) -> None:
        fields['updated_at'] = datetime.now().isoformat()
        cols = ', '.join(f"{k} = ?" for k in fields)
        await self.execute(f"UPDATE growth_state SET {cols} WHERE id = 1", tuple(fields.values()))
    
    # Memories
    async def save_memory(self, memory: Dict[str, Any]) -> str:
        memory['id'] = memory.get('id', str(uuid4()))
        memory['created_at'] = memory.get('created_at', datetime.now().isoformat())
        memory['updated_at'] = datetime.now().isoformat()
        
        await self.execute("""
            INSERT INTO memories (id, content, tags, importance, emotion, source, memory_type, embedding, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                content = excluded.content,
                tags = excluded.tags,
                importance = excluded.importance,
                emotion = excluded.emotion,
                source = excluded.source,
                memory_type = excluded.memory_type,
                embedding = excluded.embedding,
                updated_at = excluded.updated_at
        """, (
            memory['id'], memory['content'], json.dumps(memory.get('tags', [])),
            memory.get('importance', 50), memory.get('emotion', 'neutral'),
            memory.get('source', 'conversation'), memory.get('memory_type', 'semantic'),
            memory.get('embedding'), memory['created_at'], memory['updated_at']
        ))
        return memory['id']
    
    async def get_memories(self, memory_type: Optional[str] = None, limit: int = 50, 
                          min_importance: int = 0) -> List[Dict]:
        query = "SELECT * FROM memories WHERE importance >= ?"
        params = [min_importance]
        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)
        query += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)
        
        rows = await self.fetch_all(query, tuple(params))
        return [dict(row) for row in rows]
    
    async def search_memories(self, query_text: str, limit: int = 20) -> List[Dict]:
        """Simple text search - will be upgraded to embedding search."""
        rows = await self.fetch_all("""
            SELECT * FROM memories 
            WHERE content LIKE ? OR tags LIKE ?
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
        """, (f"%{query_text}%", f"%{query_text}%", limit))
        return [dict(row) for row in rows]
    
    # Scheduled events
    async def save_scheduled_event(self, event: Dict[str, Any]) -> str:
        event['id'] = event.get('id', str(uuid4()))
        event['created_at'] = event.get('created_at', datetime.now().isoformat())
        
        await self.execute("""
            INSERT INTO scheduled_events (id, trigger_type, fires_at, recurrence_rule, 
                                        condition_spec, message, status, created_at, fired_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                trigger_type = excluded.trigger_type,
                fires_at = excluded.fires_at,
                recurrence_rule = excluded.recurrence_rule,
                condition_spec = excluded.condition_spec,
                message = excluded.message,
                status = excluded.status,
                fired_at = excluded.fired_at
        """, (
            event['id'], event['trigger_type'], event.get('fires_at'),
            event.get('recurrence_rule'), json.dumps(event.get('condition_spec', {})),
            event['message'], event.get('status', 'pending'),
            event['created_at'], event.get('fired_at')
        ))
        return event['id']
    
    async def get_pending_events(self, before: datetime) -> List[Dict]:
        rows = await self.fetch_all("""
            SELECT * FROM scheduled_events 
            WHERE status = 'pending' AND fires_at <= ?
            ORDER BY fires_at ASC
        """, (before.isoformat(),))
        return [dict(row) for row in rows]
    
    async def mark_event_fired(self, event_id: str) -> None:
        await self.execute("""
            UPDATE scheduled_events SET status = 'fired', fired_at = ? WHERE id = ?
        """, (datetime.now().isoformat(), event_id))
    
    # System actions log
    async def log_action(self, action: Dict[str, Any]) -> str:
        action['id'] = action.get('id', str(uuid4()))
        action['timestamp'] = action.get('timestamp', datetime.now().isoformat())
        
        await self.execute("""
            INSERT INTO system_actions_log (id, action_type, target, backend, 
                                          initiated_by, permission_tier, result, error_message, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            action['id'], action['action_type'], action.get('target'),
            action.get('backend'), action.get('initiated_by', 'user'),
            action.get('permission_tier'), action.get('result'),
            action.get('error_message'), action['timestamp']
        ))
        return action['id']
    
    async def get_recent_actions(self, limit: int = 100) -> List[Dict]:
        rows = await self.fetch_all("""
            SELECT * FROM system_actions_log ORDER BY timestamp DESC LIMIT ?
        """, (limit,))
        return [dict(row) for row in rows]
    
    # Permissions
    async def get_permissions(self) -> Dict[str, bool]:
        row = await self.fetch_one("SELECT * FROM permissions WHERE id = 1")
        if row:
            return {k: bool(row[k]) for k in row.keys() if k not in ('id', 'updated_at')}
        return {}
    
    async def update_permissions(self, **perms) -> None:
        perms['updated_at'] = datetime.now().isoformat()
        cols = ', '.join(f"{k} = ?" for k in perms)
        await self.execute(f"UPDATE permissions SET {cols} WHERE id = 1", tuple(perms.values()))
    
    # System events
    async def add_system_event(self, event_type: str, description: str) -> str:
        event_id = str(uuid4())
        timestamp = datetime.now().isoformat()
        await self.execute("""
            INSERT INTO system_events (id, event_type, description, timestamp)
            VALUES (?, ?, ?, ?)
        """, (event_id, event_type, description, timestamp))
        return event_id

    async def get_system_events(self, event_type: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get system events, optionally filtered by event_type."""
        if event_type:
            rows = await self.fetch_all("""
                SELECT * FROM system_events WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?
            """, (event_type, limit))
        else:
            rows = await self.fetch_all("""
                SELECT * FROM system_events ORDER BY timestamp DESC LIMIT ?
            """, (limit,))
        return [dict(r) for r in rows]

    # Dialogue & Conversation persistence
    async def create_session(self, session_id: Optional[str] = None, summary: Optional[str] = None) -> str:
        """Create a new dialogue session."""
        sess_id = session_id or str(uuid4())
        now = datetime.now().isoformat()
        await self.execute("""
            INSERT INTO conversation_sessions (id, started_at, summary)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                summary = COALESCE(excluded.summary, conversation_sessions.summary)
        """, (sess_id, now, summary))
        return sess_id

    async def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session metadata."""
        row = await self.fetch_one("SELECT * FROM conversation_sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    async def end_session(self, session_id: str, summary: Optional[str] = None) -> None:
        """End a conversation session."""
        now = datetime.now().isoformat()
        if summary:
            await self.execute("""
                UPDATE conversation_sessions SET ended_at = ?, summary = ? WHERE id = ?
            """, (now, summary, session_id))
        else:
            await self.execute("""
                UPDATE conversation_sessions SET ended_at = ? WHERE id = ?
            """, (now, session_id))

    async def save_message(self, message: Dict[str, Any]) -> str:
        """Save a conversation turn (user, assistant, or system)."""
        msg_id = message.get("id", str(uuid4()))
        sess_id = message.get("session_id", "default_session")
        now = datetime.now().isoformat()
        timestamp = message.get("timestamp", now)

        # Ensure session exists to uphold foreign key integrity
        await self.create_session(session_id=sess_id)

        await self.execute("""
            INSERT INTO conversation_messages (
                id, session_id, role, content, intent, action_taken, provider, model, latency_ms, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                content = excluded.content,
                intent = excluded.intent,
                action_taken = excluded.action_taken,
                provider = excluded.provider,
                model = excluded.model,
                latency_ms = excluded.latency_ms,
                timestamp = excluded.timestamp
        """, (
            msg_id,
            sess_id,
            message.get("role", "user"),
            message.get("content", ""),
            message.get("intent"),
            message.get("action_taken"),
            message.get("provider"),
            message.get("model"),
            message.get("latency_ms"),
            timestamp,
        ))
        return msg_id

    async def get_recent_messages(self, limit: int = 10, session_id: Optional[str] = None) -> List[Dict]:
        """Get recent dialogue turns in chronological order."""
        if session_id:
            rows = await self.fetch_all("""
                SELECT * FROM conversation_messages 
                WHERE session_id = ?
                ORDER BY timestamp DESC LIMIT ?
            """, (session_id, limit))
        else:
            rows = await self.fetch_all("""
                SELECT * FROM conversation_messages 
                ORDER BY timestamp DESC LIMIT ?
            """, (limit,))
        messages = [dict(r) for r in rows]
        messages.reverse()
        return messages

    # User Preferences
    async def set_preference(self, key: str, value: Any) -> None:
        """Persist user preference setting."""
        val_str = json.dumps(value) if not isinstance(value, str) else value
        now = datetime.now().isoformat()
        await self.execute("""
            INSERT INTO user_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
        """, (key, val_str, now))

    async def get_preference(self, key: str, default: Any = None) -> Any:
        """Retrieve persisted user preference."""
        row = await self.fetch_one("SELECT value FROM user_preferences WHERE key = ?", (key,))
        if not row:
            return default
        raw = row["value"]
        try:
            return json.loads(raw)
        except Exception:
            return raw

    async def get_all_preferences(self) -> Dict[str, Any]:
        """Retrieve all user preferences as dictionary."""
        rows = await self.fetch_all("SELECT key, value FROM user_preferences")
        result = {}
        for r in rows:
            raw = r["value"]
            try:
                result[r["key"]] = json.loads(raw)
            except Exception:
                result[r["key"]] = raw
        return result

    async def commit(self) -> None:
        """Commit pending transactions."""
        async with self._lock:
            if self._conn:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._conn.commit)

    async def close(self) -> None:
        if self._conn:
            try:
                self._conn.commit()
            except Exception:
                pass
            await asyncio.get_event_loop().run_in_executor(None, self._conn.close)
            self._conn = None
            logger.info("Database connection closed")


# Global database instance
db: Optional[Database] = None


async def get_database() -> Database:
    """Get or create global database instance."""
    global db
    if db is None:
        from serein_core.config import load_config
        cfg = load_config()
        db = Database(
            path=cfg.database.path,
            encryption_key=cfg.database.encryption_key,
            pragmas=cfg.database.pragmas
        )
        await db.connect()
    return db