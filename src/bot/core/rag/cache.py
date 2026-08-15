"""EmbeddingCache — 磁盘嵌入缓存：内容哈希 → 向量，避免重复调嵌入 API。

群聊里重复文本很多（"收到""好的"等）。嵌入结果是确定性的，按
(model, 清洗后文本) 哈希作 key 落盘，命中直接返回，不产生网络调用。
"""

import json
import logging
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS embed_cache (
    key    TEXT PRIMARY KEY,
    model  TEXT NOT NULL,
    text   TEXT NOT NULL,
    vector TEXT NOT NULL,
    ts     INTEGER NOT NULL
)
"""


class EmbeddingCache:
    """线程安全的嵌入向量缓存。同步 API，由调用方包装到线程执行。"""

    def __init__(self, db_path: str, max_entries: int = 20000) -> None:
        self.db_path = db_path
        self.max_entries = max_entries
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            self.conn.execute(CREATE_SQL)
            self.conn.commit()
            logger.info("EmbeddingCache ready (db=%s, max_entries=%d)", self.db_path, self.max_entries)

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    def get(self, key: str) -> list[float] | None:
        with self._lock:
            return self._get_unlocked(key)

    def _get_unlocked(self, key: str) -> list[float] | None:
        row = self.conn.execute(
            "SELECT vector FROM embed_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set(self, key: str, model: str, text: str, vector: list[float]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO embed_cache (key, model, text, vector, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, model, text, json.dumps(vector), int(time.time())),
            )
            self.conn.commit()
            self._enforce_retention(self.conn)

    def mget(self, keys: list[str]) -> list[list[float] | None]:
        """批量读取，返回与 ``keys`` 等长列表，未命中处为 None。"""
        with self._lock:
            return [self._get_unlocked(k) for k in keys]

    def mset(
        self, pairs: list[tuple[str, str, str, list[float]]]
    ) -> None:
        """批量写入，每项 (key, model, text, vector)。"""
        if not pairs:
            return
        with self._lock:
            now = int(time.time())
            self.conn.executemany(
                "INSERT OR REPLACE INTO embed_cache (key, model, text, vector, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                [(k, m, t, json.dumps(v), now) for k, m, t, v in pairs],
            )
            self.conn.commit()
            self._enforce_retention(self.conn)

    def _enforce_retention(self, conn: sqlite3.Connection) -> None:
        """超过 max_entries 时删除最旧的记录（按写入时间）。"""
        if self.max_entries <= 0:
            return
        row = conn.execute("SELECT COUNT(*) FROM embed_cache").fetchone()
        if row[0] <= self.max_entries:
            return
        conn.execute(
            "DELETE FROM embed_cache WHERE key IN ("
            "  SELECT key FROM embed_cache ORDER BY ts ASC LIMIT ?)",
            (row[0] - self.max_entries,),
        )
        conn.commit()

    def count(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
