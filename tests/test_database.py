"""DatabaseManager 单元测试。"""


from bot.package.core.database import DatabaseManager


def test_database_manager_paths(tmp_path):
    db = DatabaseManager(str(tmp_path / "db"))
    assert db.checkpoint_path.endswith("checkpoint.sqlite")
    assert db.memory_path.endswith("memory.sqlite")
    assert db.embed_cache_path.endswith("embed_cache.sqlite")
    assert db.milvus_uri.endswith("milvus.db")


def test_database_manager_ensure_ready_creates_dir(tmp_path):
    target = tmp_path / "nested" / "db"
    db = DatabaseManager(str(target))
    assert not target.exists()
    db.ensure_ready()
    assert target.is_dir()
    db.close()
    assert target.is_dir()
