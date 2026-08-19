"""MemoryStore 单元测试：真实 langgraph AsyncSqliteStore 后端。

覆盖 store_memory / load_memories / delete / clear / format / 旧数据迁移。
注意：AsyncSqliteStore 在构造时捕获当前事件循环，每个测试用单个 asyncio.run
（一个 loop 内完成全部读写 + close），与真实 bot 常驻单 loop 一致。
"""

import asyncio
import sqlite3

from bot.package.memory import MemoryStore


def test_store_and_load_roundtrip(tmp_path):
    async def run():
        store = MemoryStore(db_dir=str(tmp_path))
        await store.store_memory("u1", "名字", "张三")
        assert await store.load_memories("u1") == [{"key": "名字", "value": "张三"}]
        await store.close()

    asyncio.run(run())


def test_isolation_between_users(tmp_path):
    async def run():
        store = MemoryStore(db_dir=str(tmp_path))
        await store.store_memory("u1", "名字", "张三")
        await store.store_memory("u2", "名字", "李四")
        assert [m["value"] for m in await store.load_memories("u1")] == ["张三"]
        assert [m["value"] for m in await store.load_memories("u2")] == ["李四"]
        await store.close()

    asyncio.run(run())


def test_overwrite_same_key(tmp_path):
    async def run():
        store = MemoryStore(db_dir=str(tmp_path))
        await store.store_memory("u1", "名字", "张三")
        await store.store_memory("u1", "名字", "李四")
        mems = await store.load_memories("u1")
        assert len(mems) == 1
        assert mems[0]["value"] == "李四"
        await store.close()

    asyncio.run(run())


def test_delete_memory(tmp_path):
    async def run():
        store = MemoryStore(db_dir=str(tmp_path))
        await store.store_memory("u1", "名字", "张三")
        await store.delete_memory("u1", "名字")
        assert await store.load_memories("u1") == []
        await store.close()

    asyncio.run(run())


def test_clear_user_memories(tmp_path):
    async def run():
        store = MemoryStore(db_dir=str(tmp_path))
        await store.store_memory("u1", "名字", "张三")
        await store.store_memory("u1", "喜欢的食物", "火锅")
        await store.store_memory("u2", "名字", "李四")
        await store.clear_user_memories("u1")
        assert await store.load_memories("u1") == []
        assert [m["value"] for m in await store.load_memories("u2")] == ["李四"]
        await store.close()

    asyncio.run(run())


def test_format_memories(tmp_path):
    async def run():
        store = MemoryStore(db_dir=str(tmp_path))
        assert await store.format_memories("u1") == ""
        await store.store_memory("u1", "名字", "张三")
        assert await store.format_memories("u1") == "- 名字：张三"
        await store.close()

    asyncio.run(run())


def test_migrates_legacy_user_memories(tmp_path):
    """旧 user_memories 表 → 官方 store 表：数据保留、旧表 DROP、幂等。"""
    db = tmp_path / "memory.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE user_memories ("
        "user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "INSERT INTO user_memories (user_id, key, value) VALUES ('u1', '名字', '张三')"
    )
    conn.execute(
        "INSERT INTO user_memories (user_id, key, value) VALUES ('u1', '城市', '上海')"
    )
    conn.commit()
    conn.close()

    async def run():
        store = MemoryStore(db_dir=str(tmp_path))
        mems = await store.load_memories("u1")
        assert len(mems) == 2
        values = {m["key"]: m["value"] for m in mems}
        assert values == {"名字": "张三", "城市": "上海"}
        await store.close()

    asyncio.run(run())

    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "user_memories" not in tables  # 迁移后旧表已删除
    assert "store" in tables              # 官方 store 表已建


def test_migrate_is_idempotent(tmp_path):
    """旧表不存在时启动不报错、store 正常工作。"""
    async def run():
        store = MemoryStore(db_dir=str(tmp_path))
        await store.store_memory("u1", "名字", "张三")
        assert await store.load_memories("u1") == [{"key": "名字", "value": "张三"}]
        await store.close()

    asyncio.run(run())