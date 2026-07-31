"""RagVectorStore — sqlite-vec 向量存储与检索。

vec0 虚拟表存放向量，普通表按 rowid 关联元数据（发言频道、发言人、时间等）。
检索策略：当前群聊优先，本群命中不足时用跨群结果补齐。
"""

import json
import logging
import os
import sqlite3
import threading

import sqlite_vec

logger = logging.getLogger(__name__)

META_COLUMNS = ("thread_id", "user_id", "user_name", "content", "role", "timestamp")


class RagVectorStore:
    """线程安全的 sqlite-vec 存储。同步 API，由调用方包装到线程执行。"""

    def __init__(
        self,
        db_dir: str = "db",
        dimensions: int = 1024,
        retention_per_thread: int = 2000,
        candidate_k: int = 50,
    ) -> None:
        self.db_path = os.path.join(db_dir, "rag.sqlite")
        self.dimensions = dimensions
        self.retention_per_thread = retention_per_thread
        self.candidate_k = candidate_k
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self.conn
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS chat_embeddings USING vec0("
                f"embedding FLOAT[{self.dimensions}] distance_metric=cosine)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_embedding_meta ("
                "  rowid INTEGER PRIMARY KEY,"
                "  thread_id TEXT NOT NULL,"
                "  user_id TEXT,"
                "  user_name TEXT,"
                "  content TEXT NOT NULL,"
                "  role TEXT NOT NULL,"
                "  timestamp INTEGER NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_meta_thread "
                "ON chat_embedding_meta(thread_id, timestamp)"
            )
            conn.commit()
            logger.info("RagVectorStore ready (db=%s, dim=%d)", self.db_path, self.dimensions)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def add(self, records: list[dict]) -> None:
        """插入一批向量记录。每项: embedding, thread_id, user_id, user_name, content, role, timestamp."""
        if not records:
            return
        with self._lock:
            conn = self.conn
            for r in records:
                cur = conn.execute(
                    "INSERT INTO chat_embedding_meta"
                    " (thread_id, user_id, user_name, content, role, timestamp)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (r["thread_id"], r.get("user_id"), r.get("user_name"),
                     r["content"], r["role"], r["timestamp"]),
                )
                rowid = cur.lastrowid
                conn.execute(
                    "INSERT INTO chat_embeddings(rowid, embedding) VALUES (?, ?)",
                    (rowid, json.dumps(r["embedding"])),
                )
            affected = {r["thread_id"] for r in records}
            conn.commit()
            for tid in affected:
                self._enforce_retention(conn, tid)

    def _enforce_retention(self, conn: sqlite3.Connection, thread_id: str) -> None:
        """删除超出 per-thread 上限的最旧记录。"""
        if self.retention_per_thread <= 0:
            return
        rows = conn.execute(
            "SELECT rowid FROM chat_embedding_meta WHERE thread_id = ?"
            " ORDER BY timestamp DESC LIMIT -1 OFFSET ?",
            (thread_id, self.retention_per_thread),
        ).fetchall()
        for (rowid,) in rows:
            conn.execute("DELETE FROM chat_embeddings WHERE rowid = ?", (rowid,))
            conn.execute("DELETE FROM chat_embedding_meta WHERE rowid = ?", (rowid,))
        if rows:
            conn.commit()

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search(
        self,
        query_vector: list[float],
        thread_id: str,
        top_k: int = 5,
        score_threshold: float = 0.5,
    ) -> list[dict]:
        """按查询向量检索，当前群聊优先，不足时跨群补齐。

        返回按相似度降序的命中记录，每项含元数据与 score（1 - cosine_distance）。
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT rowid, distance FROM chat_embeddings"
                " WHERE embedding MATCH ? AND k = ?",
                (json.dumps(query_vector), self.candidate_k),
            ).fetchall()
            if not rows:
                return []

            candidates = []
            for rowid, distance in rows:
                meta = self._fetch_meta(rowid)
                if meta is not None:
                    candidates.append({**meta, "score": 1.0 - distance})

        candidates = [c for c in candidates if c["score"] >= score_threshold]

        same = [c for c in candidates if c["thread_id"] == thread_id]
        cross = [c for c in candidates if c["thread_id"] != thread_id]

        if len(same) >= top_k:
            return same[:top_k]
        result = same + cross[: max(0, top_k - len(same))]
        return result[:top_k]

    def _fetch_meta(self, rowid: int) -> dict | None:
        row = self.conn.execute(
            "SELECT thread_id, user_id, user_name, content, role, timestamp"
            " FROM chat_embedding_meta WHERE rowid = ?",
            (rowid,),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(META_COLUMNS, row))

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------

    def count(self, thread_id: str | None = None) -> int:
        with self._lock:
            if thread_id is None:
                row = self.conn.execute("SELECT COUNT(*) FROM chat_embedding_meta").fetchone()
            else:
                row = self.conn.execute(
                    "SELECT COUNT(*) FROM chat_embedding_meta WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
            return row[0]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
