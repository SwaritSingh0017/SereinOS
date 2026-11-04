"""
Memory Engine - Full memory system implementation.
Handles working memory, semantic memory, episodic memory, and emotional memory.
Includes formation, retrieval, decay, and importance weighting.
"""
from src.core.base import BaseEngine, EngineState, MemoryRecord, event_bus
from src.database.database import Database
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from enum import Enum
import json
import uuid


class MemoryType(Enum):
    WORKING = "working"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    EMOTIONAL = "emotional"


class MemoryEngine(BaseEngine):
    """Comprehensive memory system for Serein."""
    
    @property
    def engine_name(self) -> str:
        return "memory"
    
    # Memory importance thresholds
    IMPORTANCE_THRESHOLDS = {
        "auto_store": 30,      # Below this: don't store permanently
        "working_keep": 40,    # Working memory retention threshold
        "semantic_min": 50,    # Semantic memory minimum importance
        "episodic_min": 60,    # Episodic memory minimum importance
        "emotional_min": 40,   # Emotional memory minimum importance
        "core_memory": 80,     # Core memories never decay
    }
    
    # Decay rates per day (importance loss per day)
    DECAY_RATES = {
        MemoryType.WORKING: 50,      # Fast decay
        MemoryType.SEMANTIC: 2,      # Slow decay
        MemoryType.EPISODIC: 1,      # Very slow decay
        MemoryType.EMOTIONAL: 3,     # Moderate decay
    }
    
    # Maximum memories per type (for performance)
    MAX_MEMORIES = {
        MemoryType.WORKING: 20,
        MemoryType.SEMANTIC: 500,
        MemoryType.EPISODIC: 200,
        MemoryType.EMOTIONAL: 100,
    }
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._working_memory: List[MemoryRecord] = []
        self._memory_cache: Dict[int, MemoryRecord] = {}
        self._last_decay_check: datetime = datetime.now()
    
    def initialize(self) -> None:
        """Load memories from database."""
        if not self.db:
            self.db = Database()
        
        # Load all memories and categorize
        all_memories = self.db.get_all_memories()
        for mem_row in all_memories:
            record = self._row_to_record(mem_row)
            self._memory_cache[record.id] = record
            
            # Add to working memory if recent and important
            if record.memory_type == MemoryType.WORKING.value or \
               (datetime.now() - record.created_at).days < 1 and record.importance >= self.IMPORTANCE_THRESHOLDS["working_keep"]:
                self._working_memory.append(record)
        
        # Limit working memory
        self._working_memory = self._working_memory[-self.MAX_MEMORIES[MemoryType.WORKING]:]
        
        self._last_decay_check = datetime.now()
        self._initialized = True
        event_bus.emit("memory.initialized", {
            "total_memories": len(self._memory_cache),
            "working_memory": len(self._working_memory)
        })
    
    def _row_to_record(self, row) -> MemoryRecord:
        """Converts database row to MemoryRecord."""
        # Handle tags - could be JSON or comma-separated
        tags_raw = row[2] if row[2] else []
        if isinstance(tags_raw, str):
            if tags_raw.startswith('['):
                try:
                    tags = json.loads(tags_raw)
                except:
                    tags = [t.strip() for t in tags_raw.split(',') if t.strip()]
            else:
                tags = [t.strip() for t in tags_raw.split(',') if t.strip()]
        else:
            tags = tags_raw
        
        return MemoryRecord(
            id=row[0],
            content=row[1],
            tags=tags,
            importance=row[3],
            emotion=row[4],
            source=row[5],
            created_at=datetime.fromisoformat(row[6]) if row[6] else datetime.now(),
            memory_type=row[7] if len(row) > 7 else "semantic"
        )
    
    def save_state(self) -> None:
        """Memory state is saved per-record, not as a whole."""
        pass
    
    def get_state(self) -> EngineState:
        return EngineState()
    
    # ==================== MEMORY FORMATION ====================
    
    def evaluate_and_store(self, content: str, context: Dict = None, 
                          source: str = "conversation") -> Optional[MemoryRecord]:
        """
        Evaluates content and stores as appropriate memory type.
        Returns the created MemoryRecord or None if not stored.
        """
        # Determine memory type and importance
        memory_type, importance, tags, emotion = self._analyze_content(content, context)
        
        # Check if worth storing
        if importance < self.IMPORTANCE_THRESHOLDS["auto_store"]:
            return None
        
        # Create record
        record = MemoryRecord(
            content=content,
            tags=tags,
            importance=importance,
            emotion=emotion,
            source=source,
            memory_type=memory_type.value
        )
        
        # Store in database
        record_id = self.db.save_memory(
            content=record.content,
            tags=json.dumps(record.tags),
            importance=record.importance,
            emotion=record.emotion,
            source=record.source
        )
        record.id = record_id
        
        # Add to cache
        self._memory_cache[record_id] = record
        
        # Add to working memory if appropriate
        if memory_type == MemoryType.WORKING or importance >= self.IMPORTANCE_THRESHOLDS["working_keep"]:
            self._working_memory.append(record)
            self._trim_working_memory()
        
        event_bus.emit("memory.stored", {
            "id": record_id,
            "type": memory_type.value,
            "importance": importance,
            "content": content[:100]
        })
        
        return record
    
    def _analyze_content(self, content: str, context: Dict = None) -> tuple:
        """
        Analyzes content to determine memory type, importance, tags, and emotion.
        This is a rule-based analyzer; could be enhanced with LLM.
        """
        content_lower = content.lower()
        tags = []
        emotion = "neutral"
        importance = 50
        memory_type = MemoryType.SEMANTIC
        
        # Detect identity/fact statements → Semantic
        semantic_indicators = [
            "my name is", "i am", "i work", "i study", "i like", "i prefer",
            "my favorite", "i live", "my goal", "i want to", "i'm learning"
        ]
        for indicator in semantic_indicators:
            if indicator in content_lower:
                memory_type = MemoryType.SEMANTIC
                importance = max(importance, 60)
                tags.append("identity_fact")
        
        # Detect events/experiences → Episodic
        episodic_indicators = [
            "today i", "yesterday i", "last week", "just finished", "just completed",
            "first time", "released", "launched", "passed", "failed", "achieved",
            "started", "began", "celebrated", "anniversary"
        ]
        for indicator in episodic_indicators:
            if indicator in content_lower:
                memory_type = MemoryType.EPISODIC
                importance = max(importance, 70)
                tags.append("life_event")
        
        # Detect emotional content → Emotional
        emotional_indicators = {
            "happy": "happiness", "excited": "excitement", "proud": "pride",
            "sad": "sadness", "worried": "concern", "frustrated": "concern",
            "lonely": "loneliness", "grateful": "affection", "love": "affection",
            "miss": "loneliness", "jealous": "jealousy", "possessive": "possessiveness"
        }
        for word, emo in emotional_indicators.items():
            if word in content_lower:
                memory_type = MemoryType.EMOTIONAL
                emotion = emo
                importance = max(importance, 65)
                tags.append("emotional")
        
        # Detect technical/work content
        tech_words = ["code", "program", "debug", "api", "database", "server",
                     "deploy", "git", "function", "class", "algorithm"]
        for word in tech_words:
            if word in content_lower:
                tags.append("technical")
                importance = max(importance, 55)
        
        # Detect goals/plans
        goal_words = ["goal", "plan", "want to", "going to", "will", "aim to",
                     "objective", "target", "milestone"]
        for word in goal_words:
            if word in content_lower:
                tags.append("goal")
                importance = max(importance, 60)
        
        # Context-based adjustments
        if context:
            if context.get("user_stressed"):
                importance += 10
                emotion = "concern"
            if context.get("user_happy"):
                importance += 5
                emotion = "happiness"
            if context.get("significant_event"):
                importance += 15
                memory_type = MemoryType.EPISODIC
        
        # Clamp importance
        importance = max(0, min(100, importance))
        
        # Determine final type based on importance if ambiguous
        if importance >= self.IMPORTANCE_THRESHOLDS["episodic_min"] and memory_type == MemoryType.SEMANTIC:
            memory_type = MemoryType.EPISODIC
        elif importance >= self.IMPORTANCE_THRESHOLDS["emotional_min"] and emotion != "neutral":
            memory_type = MemoryType.EMOTIONAL
        
        return memory_type, importance, tags, emotion
    
    def store_working_memory(self, content: str, importance: int = 50) -> MemoryRecord:
        """Explicitly stores something in working memory (short-term)."""
        record = MemoryRecord(
            content=content,
            tags=["working"],
            importance=importance,
            emotion="neutral",
            source="working_memory",
            memory_type=MemoryType.WORKING.value
        )
        
        self._working_memory.append(record)
        self._trim_working_memory()
        
        return record
    
    def _trim_working_memory(self) -> None:
        """Keeps working memory within limits."""
        if len(self._working_memory) > self.MAX_MEMORIES[MemoryType.WORKING]:
            # Remove oldest, lowest importance
            self._working_memory.sort(key=lambda m: (m.importance, m.created_at))
            self._working_memory = self._working_memory[-self.MAX_MEMORIES[MemoryType.WORKING]:]
    
    # ==================== MEMORY RETRIEVAL ====================
    
    def recall_relevant(self, query: str, context: Dict = None, 
                       limit: int = 10, memory_types: List[MemoryType] = None) -> List[MemoryRecord]:
        """
        Recalls memories relevant to a query.
        Uses tag matching, content similarity, and importance weighting.
        """
        if not self._memory_cache:
            self._load_all_memories()
        
        memory_types = memory_types or list(MemoryType)
        query_lower = query.lower()
        query_words = set(query_lower.split())
        
        scored_memories = []
        
        for record in self._memory_cache.values():
            if record.memory_type not in [mt.value for mt in memory_types]:
                continue
            
            score = self._calculate_relevance(record, query_lower, query_words, context)
            if score > 0:
                scored_memories.append((score, record))
        
        # Sort by score (relevance * importance * recency)
        scored_memories.sort(key=lambda x: x[0], reverse=True)
        
        return [record for score, record in scored_memories[:limit]]
    
    def _calculate_relevance(self, record: MemoryRecord, query_lower: str, 
                            query_words: set, context: Dict = None) -> float:
        """Calculates relevance score for a memory given a query."""
        score = 0.0
        
        # Tag matching (strong signal)
        for tag in record.tags:
            if tag in query_lower:
                score += 10
            for qword in query_words:
                if qword in tag or tag in qword:
                    score += 5
        
        # Content word overlap
        content_words = set(record.content.lower().split())
        overlap = len(query_words & content_words)
        score += overlap * 2
        
        # Importance weighting
        score += record.importance * 0.1
        
        # Recency bonus (memories from last 7 days get boost)
        days_old = (datetime.now() - record.created_at).days
        if days_old <= 7:
            score += (7 - days_old) * 0.5
        
        # Context boost
        if context:
            if context.get("current_topic") and context["current_topic"] in record.tags:
                score += 15
            if context.get("user_emotion") and context["user_emotion"] == record.emotion:
                score += 10
        
        # Working memory always gets a boost for current context
        if record.memory_type == MemoryType.WORKING.value:
            score += 20
        
        return score
    
    def get_working_memory(self) -> List[MemoryRecord]:
        """Returns current working memory."""
        return self._working_memory.copy()
    
    def get_memories_by_type(self, memory_type: MemoryType, limit: int = 50) -> List[MemoryRecord]:
        """Returns memories of a specific type, sorted by importance."""
        memories = [m for m in self._memory_cache.values() 
                   if m.memory_type == memory_type.value]
        memories.sort(key=lambda m: (m.importance, m.created_at), reverse=True)
        return memories[:limit]
    
    def search_memories(self, keyword: str, limit: int = 20) -> List[MemoryRecord]:
        """Simple keyword search across all memories."""
        return self.recall_relevant(keyword, limit=limit)
    
    def get_user_name(self) -> Optional[str]:
        """Extracts user name from identity memories."""
        memories = self.get_memories_by_type(MemoryType.SEMANTIC)
        for mem in memories:
            if "identity_fact" in mem.tags and "my name is" in mem.content.lower():
                return mem.content.replace("My name is", "").replace("my name is", "").strip()
        return None
    
    def get_user_profile_summary(self) -> Dict[str, Any]:
        """Builds a profile summary from semantic memories."""
        semantic_memories = self.get_memories_by_type(MemoryType.SEMANTIC)
        
        profile = {
            "facts": [],
            "preferences": [],
            "goals": [],
            "technical_interests": [],
        }
        
        for mem in semantic_memories:
            if "identity_fact" in mem.tags:
                profile["facts"].append(mem.content)
            if "goal" in mem.tags:
                profile["goals"].append(mem.content)
            if "technical" in mem.tags:
                profile["technical_interests"].append(mem.content)
            # Preferences detected by "like", "prefer", "favorite"
            if any(w in mem.content.lower() for w in ["like", "prefer", "favorite"]):
                profile["preferences"].append(mem.content)
        
        return profile
    
    def get_recent_episodes(self, days: int = 30, limit: int = 10) -> List[MemoryRecord]:
        """Returns recent episodic memories."""
        cutoff = datetime.now() - timedelta(days=days)
        episodes = [m for m in self._memory_cache.values() 
                   if m.memory_type == MemoryType.EPISODIC.value 
                   and m.created_at >= cutoff]
        episodes.sort(key=lambda m: m.created_at, reverse=True)
        return episodes[:limit]
    
    def get_emotional_memories(self, emotion: str = None, limit: int = 20) -> List[MemoryRecord]:
        """Returns emotional memories, optionally filtered by emotion."""
        memories = [m for m in self._memory_cache.values() 
                   if m.memory_type == MemoryType.EMOTIONAL.value]
        if emotion:
            memories = [m for m in memories if m.emotion == emotion]
        memories.sort(key=lambda m: (m.importance, m.created_at), reverse=True)
        return memories[:limit]
    
    # ==================== MEMORY MAINTENANCE ====================
    
    def apply_decay(self) -> int:
        """
        Applies importance decay to memories based on type and age.
        Returns number of memories affected.
        """
        now = datetime.now()
        days_since_check = (now - self._last_decay_check).days
        
        if days_since_check == 0:
            return 0
        
        affected = 0
        
        for record in self._memory_cache.values():
            # Core memories never decay
            if record.importance >= self.IMPORTANCE_THRESHOLDS["core_memory"]:
                continue
            
            mt = MemoryType(record.memory_type)
            decay_rate = self.DECAY_RATES.get(mt, 1)
            
            # Calculate decay
            total_decay = decay_rate * days_since_check
            new_importance = max(0, record.importance - total_decay)
            
            if new_importance != record.importance:
                record.importance = new_importance
                self.db.update_memory_importance(record.id, new_importance)
                affected += 1
            
            # Remove very low importance non-core memories
            if new_importance < 10 and record.importance < self.IMPORTANCE_THRESHOLDS["core_memory"]:
                self.db.delete_memory(record.id)
                del self._memory_cache[record.id]
                affected += 1
        
        # Refresh working memory
        self._refresh_working_memory()
        
        self._last_decay_check = now
        
        if affected > 0:
            event_bus.emit("memory.decay_applied", {"affected_count": affected})
        
        return affected
    
    def _refresh_working_memory(self) -> None:
        """Refreshes working memory from cache."""
        self._working_memory = [m for m in self._memory_cache.values() 
                               if m.memory_type == MemoryType.WORKING.value]
        self._trim_working_memory()
    
    def _load_all_memories(self) -> None:
        """Reloads all memories from database."""
        all_memories = self.db.get_all_memories()
        self._memory_cache = {}
        for row in all_memories:
            record = self._row_to_record(row)
            self._memory_cache[record.id] = record
    
    def promote_to_long_term(self, memory_id: int, new_type: MemoryType = None) -> bool:
        """Promotes a working memory to long-term storage."""
        if memory_id not in self._memory_cache:
            return False
        
        record = self._memory_cache[memory_id]
        
        if new_type is None:
            # Auto-determine based on content
            if "life_event" in record.tags:
                new_type = MemoryType.EPISODIC
            elif record.emotion != "neutral":
                new_type = MemoryType.EMOTIONAL
            else:
                new_type = MemoryType.SEMANTIC
        
        record.memory_type = new_type.value
        # Boost importance slightly for promotion
        record.importance = min(100, record.importance + 10)
        
        self.db.update_memory_type(memory_id, new_type.value)
        self.db.update_memory_importance(memory_id, record.importance)
        
        # Remove from working memory
        self._working_memory = [m for m in self._working_memory if m.id != memory_id]
        
        event_bus.emit("memory.promoted", {
            "id": memory_id,
            "new_type": new_type.value,
            "new_importance": record.importance
        })
        
        return True
    
    def consolidate_memories(self) -> Dict[str, int]:
        """
        Runs memory consolidation: promotes important working memories,
        merges similar memories, applies decay.
        Returns statistics.
        """
        stats = {"promoted": 0, "decayed": 0, "merged": 0}
        
        # Promote important working memories
        for record in self._working_memory[:]:
            if record.importance >= self.IMPORTANCE_THRESHOLDS["semantic_min"]:
                if self.promote_to_long_term(record.id):
                    stats["promoted"] += 1
        
        # Apply decay
        stats["decayed"] = self.apply_decay()
        
        # TODO: Merge similar memories (future enhancement)
        
        event_bus.emit("memory.consolidated", stats)
        return stats
    
    def get_memory_context(self, query: str = "", limit: int = 10) -> str:
        """Returns formatted memory context for LLM prompts."""
        relevant = self.recall_relevant(query, limit=limit) if query else \
                   self.get_working_memory()[:limit]
        
        if not relevant:
            return "MEMORIES:\n(No relevant memories)"
        
        lines = ["MEMORIES:"]
        for mem in relevant:
            type_label = mem.memory_type.upper()
            tags = f" [{', '.join(mem.tags)}]" if mem.tags else ""
            emotion = f" ({mem.emotion})" if mem.emotion != "neutral" else ""
            lines.append(f"- [{type_label}{tags}{emotion}] {mem.content}")
        
        return "\n".join(lines)
    
    def get_stats(self) -> Dict[str, Any]:
        """Returns memory system statistics."""
        by_type = {}
        for mt in MemoryType:
            by_type[mt.value] = len([m for m in self._memory_cache.values() 
                                     if m.memory_type == mt.value])
        
        return {
            "total_memories": len(self._memory_cache),
            "working_memory": len(self._working_memory),
            "by_type": by_type,
            "avg_importance": sum(m.importance for m in self._memory_cache.values()) / 
                            max(1, len(self._memory_cache)),
            "oldest_memory": min((m.created_at for m in self._memory_cache.values()), 
                               default=None),
            "newest_memory": max((m.created_at for m in self._memory_cache.values()), 
                               default=None),
        }
    
    def close(self) -> None:
        """Saves any pending changes and cleans up."""
        self.consolidate_memories()
        super().close()