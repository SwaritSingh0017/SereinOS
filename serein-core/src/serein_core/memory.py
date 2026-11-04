"""
Memory Engine - Full memory system with working, semantic, episodic, and emotional memory.
Includes formation, retrieval, decay, consolidation, and embeddings.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Dict, Optional, Any, TYPE_CHECKING
from uuid import uuid4

from serein_core.database import get_database
from serein_core.events import event_bus

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger("serein.core.memory")


class MemoryType(Enum):
    WORKING = "working"      # Short-term, current session
    SEMANTIC = "semantic"    # Facts and knowledge
    EPISODIC = "episodic"    # Events and experiences
    EMOTIONAL = "emotional"  # Emotionally charged memories


@dataclass
class MemoryRecord:
    id: str
    content: str
    tags: List[str] = field(default_factory=list)
    importance: int = 50
    emotion: str = "neutral"
    source: str = "conversation"
    memory_type: str = "semantic"
    embedding: Optional[List[float]] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    last_accessed: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "content": self.content,
            "tags": self.tags,
            "importance": self.importance,
            "emotion": self.emotion,
            "source": self.source,
            "memory_type": self.memory_type,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "MemoryRecord":
        record = cls(
            id=data["id"],
            content=data["content"],
            tags=data.get("tags", []),
            importance=data.get("importance", 50),
            emotion=data.get("emotion", "neutral"),
            source=data.get("source", "conversation"),
            memory_type=data.get("memory_type", "semantic"),
        )
        if data.get("embedding"):
            record.embedding = data["embedding"]
        if data.get("created_at"):
            record.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("updated_at"):
            record.updated_at = datetime.fromisoformat(data["updated_at"])
        if data.get("access_count") is not None:
            record.access_count = data["access_count"]
        if data.get("last_accessed"):
            record.last_accessed = datetime.fromisoformat(data["last_accessed"])
        return record


class MemoryEngine:
    """
    Comprehensive memory system with 4 memory types, embeddings, decay, and consolidation.
    """
    
    # Importance thresholds
    THRESHOLDS = {
        "auto_store": 30,
        "working_keep": 40,
        "semantic_min": 50,
        "episodic_min": 60,
        "emotional_min": 40,
        "core_memory": 80,
    }
    
    # Decay per day
    DECAY_RATES = {
        MemoryType.WORKING: 50,
        MemoryType.SEMANTIC: 2,
        MemoryType.EPISODIC: 1,
        MemoryType.EMOTIONAL: 3,
    }
    
    MAX_MEMORIES = {
        MemoryType.WORKING: 20,
        MemoryType.SEMANTIC: 500,
        MemoryType.EPISODIC: 200,
        MemoryType.EMOTIONAL: 100,
    }
    
    def __init__(self):
        self._working_memory: List[MemoryRecord] = []
        self._cache: Dict[str, MemoryRecord] = {}
        self._embedding_model = None
        self._last_decay = datetime.now()
        self._initialized = False
    
    async def initialize(self) -> None:
        """Load memories and embedding model."""
        db = await get_database()
        
        # Load all memories
        rows = await db.get_memories(limit=2000)
        for row in rows:
            record = MemoryRecord.from_dict(row)
            self._cache[record.id] = record
            
            if record.memory_type == MemoryType.WORKING.value or \
               (datetime.now() - record.created_at).days < 1 and record.importance >= self.THRESHOLDS["working_keep"]:
                self._working_memory.append(record)
        
        self._working_memory = self._working_memory[-self.MAX_MEMORIES[MemoryType.WORKING]:]
        
        # Try to load embedding model (respect SEREIN_ENABLE_EMBEDDINGS)
        import os
        if os.getenv("SEREIN_ENABLE_EMBEDDINGS", "1") != "0":
            try:
                from sentence_transformers import SentenceTransformer
                self._embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
                logger.info("Embedding model loaded")
            except Exception as e:
                logger.warning(f"Could not load embedding model: {e}")
        else:
            self._embedding_model = None
            logger.info("Embeddings disabled via SEREIN_ENABLE_EMBEDDINGS=0")
        
        self._last_decay = datetime.now()
        self._initialized = True
        logger.info(f"Memory engine initialized. Total: {len(self._cache)}, Working: {len(self._working_memory)}")
        
        await event_bus.publish(type("Event", (), {
            "type": "memory.initialized",
            "data": {"total": len(self._cache), "working": len(self._working_memory)}
        })())
    
    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding for text."""
        if self._embedding_model:
            try:
                emb = self._embedding_model.encode(text)
                return emb.tolist()
            except Exception as e:
                logger.error(f"Embedding error: {e}")
        return None
    
    async def evaluate_and_store(
        self,
        content: str,
        context: Optional[Dict] = None,
        source: str = "conversation",
        memory_type: Optional[MemoryType] = None,
        importance: Optional[int] = None,
    ) -> Optional[MemoryRecord]:
        """Evaluate content and store as appropriate memory type."""
        norm_content = content.strip()
        # Check duplicate
        for existing in self._cache.values():
            if existing.content.strip().lower() == norm_content.lower():
                existing.importance = min(100, existing.importance + 5)
                existing.updated_at = datetime.now()
                db = await get_database()
                await db.save_memory(existing.to_dict())
                return existing

        analyzed_type, analyzed_imp, tags, emotion = self._analyze(content, context)
        m_type = memory_type if memory_type is not None else analyzed_type
        imp = importance if importance is not None else analyzed_imp

        if imp < self.THRESHOLDS["auto_store"]:
            return None

        embedding = await self._get_embedding(content)
        m_type_val = m_type.value if isinstance(m_type, MemoryType) else str(m_type)

        record = MemoryRecord(
            id=str(uuid4()),
            content=content,
            tags=tags,
            importance=imp,
            emotion=emotion,
            source=source,
            memory_type=m_type_val,
            embedding=embedding,
        )

        db = await get_database()
        await db.save_memory(record.to_dict())

        self._cache[record.id] = record

        if m_type == MemoryType.WORKING or m_type_val == MemoryType.WORKING.value or imp >= self.THRESHOLDS["working_keep"]:
            self._working_memory.append(record)
            self._trim_working()

        await event_bus.publish(type("Event", (), {
            "type": "memory.stored",
            "data": {"id": record.id, "type": m_type_val, "importance": imp}
        })())

        return record
    
    def _analyze(self, content: str, context: Dict = None) -> tuple:
        """Analyze content to determine memory type, importance, tags, emotion."""
        content_lower = content.lower()
        tags = []
        emotion = "neutral"
        importance = 50
        memory_type = MemoryType.SEMANTIC
        
        # Semantic indicators
        semantic_indicators = [
            "my name is", "i am", "i work", "i study", "i like", "i prefer",
            "my favorite", "i live", "my goal", "i want to", "i'm learning"
        ]
        for ind in semantic_indicators:
            if ind in content_lower:
                importance = max(importance, 60)
                tags.append("identity_fact")
        
        # Episodic indicators
        episodic_indicators = [
            "today i", "yesterday i", "last week", "just finished", "just completed",
            "first time", "released", "launched", "passed", "failed", "achieved",
            "started", "began", "celebrated", "anniversary"
        ]
        for ind in episodic_indicators:
            if ind in content_lower:
                memory_type = MemoryType.EPISODIC
                importance = max(importance, 70)
                tags.append("life_event")
        
        # Emotional indicators
        emotional_map = {
            "happy": "happiness", "excited": "excitement", "proud": "pride",
            "sad": "sadness", "worried": "concern", "frustrated": "concern",
            "lonely": "loneliness", "grateful": "affection", "love": "affection",
            "miss": "loneliness", "jealous": "jealousy", "possessive": "possessiveness"
        }
        for word, emo in emotional_map.items():
            if word in content_lower:
                memory_type = MemoryType.EMOTIONAL
                emotion = emo
                importance = max(importance, 65)
                tags.append("emotional")
        
        # Technical
        tech_words = ["code", "program", "debug", "api", "database", "server",
                     "deploy", "git", "function", "class", "algorithm"]
        for word in tech_words:
            if word in content_lower:
                tags.append("technical")
                importance = max(importance, 55)
        
        # Goals
        goal_words = ["goal", "plan", "want to", "going to", "will", "aim to",
                     "objective", "target", "milestone"]
        for word in goal_words:
            if word in content_lower:
                tags.append("goal")
                importance = max(importance, 60)
        
        # Context
        if context:
            if context.get("user_stressed"):
                importance += 10; emotion = "concern"
            if context.get("user_happy"):
                importance += 5; emotion = "happiness"
            if context.get("significant_event"):
                importance += 15; memory_type = MemoryType.EPISODIC
        
        importance = max(0, min(100, importance))
        
        if importance >= self.THRESHOLDS["episodic_min"] and memory_type == MemoryType.SEMANTIC and "identity_fact" not in tags:
            memory_type = MemoryType.EPISODIC
        elif importance >= self.THRESHOLDS["emotional_min"] and emotion != "neutral":
            memory_type = MemoryType.EMOTIONAL
        
        return memory_type, importance, tags, emotion
    
    def _trim_working(self) -> None:
        if len(self._working_memory) > self.MAX_MEMORIES[MemoryType.WORKING]:
            self._working_memory.sort(key=lambda m: (m.importance, m.created_at))
            self._working_memory = self._working_memory[-self.MAX_MEMORIES[MemoryType.WORKING]:]
    
    async def recall(self, query: str, context: Dict = None, 
                    limit: int = 10, types: List[MemoryType] = None) -> List[MemoryRecord]:
        """Recall relevant memories using embeddings + keyword matching."""
        if not self._cache:
            return []
        
        types = types or list(MemoryType)
        query_lower = query.lower()
        query_words = set(query_lower.split())
        
        scored = []
        query_emb = await self._get_embedding(query) if self._embedding_model else None
        
        for record in self._cache.values():
            if record.memory_type not in [t.value for t in types]:
                continue
            
            score = 0.0
            
            # Tag matching
            for tag in record.tags:
                if tag in query_lower:
                    score += 10
                for qw in query_words:
                    if qw in tag or tag in qw:
                        score += 5
            
            # Content word overlap
            content_words = set(record.content.lower().split())
            score += len(query_words & content_words) * 2
            
            # Embedding similarity
            if query_emb and record.embedding:
                import numpy as np
                sim = np.dot(query_emb, record.embedding) / (
                    np.linalg.norm(query_emb) * np.linalg.norm(record.embedding) + 1e-8)
                score += sim * 20
            
            # Importance
            score += record.importance * 0.1
            
            # Recency
            days = (datetime.now() - record.created_at).days
            if days <= 7:
                score += (7 - days) * 0.5
            
            # Working memory boost
            if record.memory_type == MemoryType.WORKING.value:
                score += 20
            
            # Context boost
            if context:
                if context.get("current_topic") in record.tags:
                    score += 15
                if context.get("user_emotion") == record.emotion:
                    score += 10
            
            if score > 0:
                scored.append((score, record))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _, r in scored[:limit]]
        db = await get_database()
        now_dt = datetime.now()
        for r in results:
            r.access_count += 1
            r.last_accessed = now_dt
            try:
                await db.save_memory(r.to_dict())
            except Exception:
                pass
        return results
    
    async def get_working(self) -> List[MemoryRecord]:
        return self._working_memory.copy()
    
    async def get_by_type(self, mtype: MemoryType, limit: int = 50) -> List[MemoryRecord]:
        mem = [m for m in self._cache.values() if m.memory_type == mtype.value]
        mem.sort(key=lambda m: (m.importance, m.created_at), reverse=True)
        return mem[:limit]
    
    async def search(self, keyword: str, limit: int = 20) -> List[MemoryRecord]:
        return await self.recall(keyword, limit=limit)
    
    async def get_user_name(self) -> Optional[str]:
        mem = await self.get_by_type(MemoryType.SEMANTIC)
        for m in mem:
            if "identity_fact" in m.tags and "my name is" in m.content.lower():
                return m.content.replace("My name is", "").replace("my name is", "").strip()
        return None
    
    async def get_profile_summary(self) -> Dict:
        sem = await self.get_by_type(MemoryType.SEMANTIC)
        return {
            "facts": [m.content for m in sem if "identity_fact" in m.tags],
            "goals": [m.content for m in sem if "goal" in m.tags],
            "technical": [m.content for m in sem if "technical" in m.tags],
            "preferences": [m.content for m in sem if any(w in m.content.lower() for w in ["like", "prefer", "favorite"])],
        }
    
    async def apply_decay(self, force: bool = False) -> int:
        """Apply importance decay based on memory type and age."""
        now = datetime.now()
        days = (now - self._last_decay).days

        if not force and days == 0:
            return 0

        affected = 0
        for record_id, record in list(self._cache.items()):
            if record.importance >= self.THRESHOLDS["core_memory"]:
                continue

            try:
                mtype = MemoryType(record.memory_type)
            except ValueError:
                mtype = MemoryType.SEMANTIC
            rate = self.DECAY_RATES.get(mtype, 1)

            rec_created = record.created_at
            if hasattr(rec_created, "tzinfo") and rec_created.tzinfo is not None:
                from datetime import timezone
                effective_now = datetime.now(timezone.utc)
                age_days = max(1, (effective_now - rec_created).days)
            else:
                age_days = max(1, (now - rec_created).days)

            decay = rate * (age_days if force else max(1, days))
            new_imp = max(0, record.importance - decay)

            if new_imp != record.importance:
                record.importance = new_imp
                affected += 1

            if new_imp < 10 and record.importance < self.THRESHOLDS["core_memory"]:
                db = await get_database()
                await db.execute("DELETE FROM memories WHERE id = ?", (record.id,))
                del self._cache[record.id]
                affected += 1

        self._last_decay = now

        if affected:
            await event_bus.publish(type("Event", (), {
                "type": "memory.decay",
                "data": {"affected": affected}
            })())

        return affected
    
    async def consolidate(self) -> Dict[str, int]:
        """Consolidate: promote working, decay, merge similar."""
        stats = {"promoted": 0, "decayed": 0}
        
        # Promote important working memories
        for record in self._working_memory[:]:
            if record.importance >= self.THRESHOLDS["semantic_min"]:
                record.memory_type = MemoryType.SEMANTIC.value
                record.importance = min(100, record.importance + 10)
                self._working_memory.remove(record)
                stats["promoted"] += 1
        
        stats["decayed"] = await self.apply_decay()
        
        await event_bus.publish(type("Event", (), {
            "type": "memory.consolidated",
            "data": stats
        })())
        
        return stats
    
    async def get_context(self, query: str = "", limit: int = 10) -> str:
        relevant = await self.recall(query, limit=limit) if query else self._working_memory[:limit]
        
        if not relevant:
            return "MEMORIES:\n(No relevant memories)"
        
        lines = ["MEMORIES:"]
        for m in relevant:
            t = m.memory_type.upper()
            tags = f" [{', '.join(m.tags)}]" if m.tags else ""
            emo = f" ({m.emotion})" if m.emotion != "neutral" else ""
            lines.append(f"- [{t}{tags}{emo}] {m.content}")
        
        return "\n".join(lines)
    
    async def get_stats(self) -> Dict:
        by_type = {}
        for mt in MemoryType:
            by_type[mt.value] = len([m for m in self._cache.values() if m.memory_type == mt.value])

        return {
            "initialized": self._initialized,
            "embeddings_enabled": bool(self._embedding_model is not None),
            "total": len(self._cache),
            "working": len(self._working_memory),
            "by_type": by_type,
            "avg_importance": sum(m.importance for m in self._cache.values()) / max(1, len(self._cache)),
        }


memory_engine = MemoryEngine()