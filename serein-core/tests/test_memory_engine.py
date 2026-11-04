import asyncio
from datetime import datetime, timezone, timedelta

import pytest
import sqlite3
import sys
import types

# The production database prefers SQLCipher; unit tests use stdlib sqlite3.
sqlcipher_stub = types.ModuleType("sqlcipher3")
sqlcipher_stub.dbapi2 = sqlite3
sys.modules.setdefault("sqlcipher3", sqlcipher_stub)

import serein_core.memory as memory_module
from serein_core.memory import MemoryEngine, MemoryType


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.executed = []

    async def fetch_all(self, query, params=()):
        if "PRAGMA table_info(memories)" in query:
            names = ["id", "content", "tags", "importance", "emotion", "source", "memory_type", "embedding", "created_at", "updated_at", "last_accessed", "access_count"]
            return [(i, n) for i, n in enumerate(names)]
        rows = list(self.rows.values())
        return rows

    async def execute(self, query, params=()):
        self.executed.append((query, params))
        if query.startswith("DELETE FROM memories"):
            self.rows.pop(params[0], None)

    async def get_memories(self, memory_type=None, limit=50, min_importance=0):
        rows = [r for r in self.rows.values() if int(r.get("importance", 50)) >= min_importance]
        if memory_type:
            rows = [r for r in rows if r.get("memory_type") == memory_type]
        return sorted(rows, key=lambda r: (r.get("importance", 50), r.get("created_at", "")), reverse=True)[:limit]

    async def save_memory(self, memory):
        self.rows[memory["id"]] = dict(memory)
        return memory["id"]


@pytest.fixture
def fake_db(monkeypatch):
    monkeypatch.setenv("SEREIN_ENABLE_EMBEDDINGS", "0")
    db = FakeDB()
    monkeypatch.setattr(memory_module, "get_database", lambda: _return(db))
    return db


async def _return(value):
    return value


@pytest.mark.asyncio
async def test_initialize_is_offline_and_loads_memory(fake_db, monkeypatch):
    engine = MemoryEngine()
    monkeypatch.setenv("SEREIN_ENABLE_EMBEDDINGS", "0")
    await engine.initialize()
    assert engine.get_stats
    stats = await engine.get_stats()
    assert stats["initialized"] is True
    assert stats["total"] == 0
    assert stats["embeddings_enabled"] is False


@pytest.mark.asyncio
async def test_store_classifies_identity_and_persists(fake_db):
    engine = MemoryEngine()
    await engine.initialize()
    record = await engine.evaluate_and_store("My name is Swarit")
    assert record is not None
    assert record.memory_type == MemoryType.SEMANTIC.value
    assert "identity_fact" in record.tags
    assert await engine.get_user_name() == "Swarit"
    assert record.id in fake_db.rows


@pytest.mark.asyncio
async def test_duplicate_reinforces_instead_of_creating_spam(fake_db):
    engine = MemoryEngine()
    await engine.initialize()
    first = await engine.evaluate_and_store("I prefer Java")
    second = await engine.evaluate_and_store("  I PREFER Java  ")
    assert first.id == second.id
    assert len(fake_db.rows) == 1
    assert second.importance > first.importance - 1


@pytest.mark.asyncio
async def test_recall_updates_access_metadata(fake_db):
    engine = MemoryEngine()
    await engine.initialize()
    record = await engine.evaluate_and_store("I study computer science and Java")
    results = await engine.recall("Java")
    assert results and results[0].id == record.id
    assert results[0].access_count == 1
    assert results[0].last_accessed is not None
    assert fake_db.rows[record.id]["access_count"] == 1


@pytest.mark.asyncio
async def test_working_memory_is_bounded(fake_db):
    engine = MemoryEngine()
    await engine.initialize()
    for i in range(30):
        await engine.evaluate_and_store(f"temporary working note {i}", memory_type=MemoryType.WORKING, importance=40)
    assert len(await engine.get_working()) <= engine.MAX_MEMORIES[MemoryType.WORKING]


@pytest.mark.asyncio
async def test_consolidate_promotes_working_memory(fake_db):
    engine = MemoryEngine()
    await engine.initialize()
    record = await engine.evaluate_and_store("Important project milestone", memory_type=MemoryType.WORKING, importance=80)
    stats = await engine.consolidate()
    assert stats["promoted"] == 1
    assert record.memory_type == MemoryType.SEMANTIC.value


@pytest.mark.asyncio
async def test_decay_does_not_delete_core_memory(fake_db):
    engine = MemoryEngine()
    await engine.initialize()
    record = await engine.evaluate_and_store("My core identity fact", importance=90)
    record.created_at = datetime.now(timezone.utc) - timedelta(days=365)
    affected = await engine.apply_decay(force=True)
    assert affected == 0
    assert record.id in engine._cache


@pytest.mark.asyncio
async def test_context_format(fake_db):
    engine = MemoryEngine()
    await engine.initialize()
    await engine.evaluate_and_store("I like Java", importance=70)
    context = await engine.get_context("Java")
    assert context.startswith("MEMORIES:")
    assert "I like Java" in context
