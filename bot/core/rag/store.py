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

# 新版 meta 列：显式建模发送者/接收者（含 bot），替代旧的 user_id/user_name/role。
# 查询入口是昵称（LLM 只认识昵称），故 sender_name/receiver_name 为必填语义列，
# *_id 保留稳定键用于追踪改名。role 被蕴含（sender==bot 名即 bot 发言）。
META_COLUMNS = (
    "thread_id", "sender_id", "sender_name",
    "receiver_id", "receiver_name", "content", "timestamp",
)

# 旧版 schema 的标志：role 时代列（user_id/user_name/role）与 epoch 整数时间戳
# （timestamp INTEGER）。任一命中即视为不兼容，DROP 重建（丢弃历史）。
LEGACY_COLUMNS = ("user_id", "user_name", "role")


def _escape_like(text: str) -> str:
    """转义 LIKE 模式里的通配符（配合 ESCAPE '\\' 使用）。"""
    return (
        text.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


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
            self._drop_legacy_schema(conn)
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS chat_embeddings USING vec0("
                f"embedding FLOAT[{self.dimensions}] distance_metric=cosine)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_embedding_meta ("
                "  rowid INTEGER PRIMARY KEY,"
                "  thread_id TEXT NOT NULL,"
                "  sender_id TEXT,"
                "  sender_name TEXT NOT NULL,"
                "  receiver_id TEXT,"
                "  receiver_name TEXT NOT NULL DEFAULT '',"
                "  content TEXT NOT NULL,"
                "  timestamp TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_meta_thread "
                "ON chat_embedding_meta(thread_id, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_meta_sender "
                "ON chat_embedding_meta(thread_id, sender_name)"
            )
            conn.commit()
            logger.info("RagVectorStore ready (db=%s, dim=%d)", self.db_path, self.dimensions)

    def _drop_legacy_schema(self, conn: sqlite3.Connection) -> None:
        """不兼容的旧 meta schema → 新版重建，丢弃历史。

        触发条件：role 时代列（user_id/user_name/role）或 epoch 整数时间戳
        （timestamp INTEGER，现为 TEXT ISO）。旧 assistant 记录没有存 bot 名、
        epoch 整数与新 ISO 字符串混列比较全坏，两者都无法正确迁移；rag 是辅助
        检索缓存，直接重建最干净。vec0 与 meta 按 rowid 关联，须两张一起 DROP，
        否则残留孤儿向量。
        """
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='chat_embedding_meta'"
        ).fetchone()
        if row is None:
            return  # 表尚不存在（首次建库），走正常 CREATE
        schema = (row[0] or "").lower()
        if any(col in schema for col in LEGACY_COLUMNS) or "timestamp integer" in schema:
            conn.execute("DROP TABLE IF EXISTS chat_embeddings")
            conn.execute("DROP TABLE IF EXISTS chat_embedding_meta")
            logger.warning("Dropped legacy chat_embedding schema; rebuilding with current schema")

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def add(self, records: list[dict]) -> None:
        """插入一批向量记录。每项: embedding, thread_id, sender_id, sender_name, receiver_id, receiver_name, content, timestamp."""
        if not records:
            return
        with self._lock:
            conn = self.conn
            for r in records:
                cur = conn.execute(
                    "INSERT INTO chat_embedding_meta"
                    " (thread_id, sender_id, sender_name, receiver_id, receiver_name, content, timestamp)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["thread_id"], r.get("sender_id"), r["sender_name"],
                     r.get("receiver_id"), r.get("receiver_name", ""),
                     r["content"], r["timestamp"]),
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
        since_iso: str = "",
        until_iso: str = "",
    ) -> list[dict]:
        """按查询向量检索，当前群聊优先，不足时跨群补齐。

        可选时间窗口 ``since_iso``/``until_iso``（ISO 字符串）过滤候选
        （vec0 不支持 meta 过滤，检索后按 timestamp 剪枝）。返回按相似度
        降序的命中记录，每项含元数据与 score（1 - cosine_distance）。
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
                if meta is None:
                    continue
                if since_iso or until_iso:
                    ts = meta["timestamp"]
                    if since_iso and ts < since_iso:
                        continue
                    if until_iso and ts > until_iso:
                        continue
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
            "SELECT thread_id, sender_id, sender_name, receiver_id, receiver_name, content, timestamp"
            " FROM chat_embedding_meta WHERE rowid = ?",
            (rowid,),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(META_COLUMNS, row))

    # ------------------------------------------------------------------
    # 属性检索（无 embedding）
    # ------------------------------------------------------------------

    def query_meta(
        self,
        thread_id: str,
        person: str = "",
        content_keyword: str = "",
        since_iso: str = "",
        until_iso: str = "",
        limit: int = 10,
    ) -> list[dict]:
        """按发送者/接收者昵称 + 内容关键词 + 时间窗口检索（纯 SQL，无向量）。

        ``person`` 模糊匹配 sender_name 或 receiver_name（OR）——回答"张三说了
        什么 / bot 回了张三什么"。``content_keyword`` 匹配内容子串——回答"谁说过 xx"。
        时间窗口是 ``YYYY-MM-DD HH:MM:SS`` 字符串（定宽零填充，字典序==时间序，
        可直接 >= / <= 比较）；``since_iso``/``until_iso`` 可单独给，留空即不设该边界。
        均用 LIKE + ESCAPE 转义通配符。
        """
        with self._lock:
            conds, args = ["thread_id = ?"], [thread_id]
            if since_iso:
                conds.append("timestamp >= ?")
                args.append(since_iso)
            if until_iso:
                conds.append("timestamp <= ?")
                args.append(until_iso)
            if person:
                esc = _escape_like(person)
                conds.append(
                    "(sender_name LIKE ? ESCAPE '\\' OR receiver_name LIKE ? ESCAPE '\\')"
                )
                args += [f"%{esc}%", f"%{esc}%"]
            if content_keyword:
                esc = _escape_like(content_keyword)
                conds.append("content LIKE ? ESCAPE '\\'")
                args.append(f"%{esc}%")
            rows = self.conn.execute(
                "SELECT thread_id, sender_id, sender_name, receiver_id, receiver_name, content, timestamp"
                " FROM chat_embedding_meta"
                f" WHERE {' AND '.join(conds)} ORDER BY timestamp DESC LIMIT ?",
                args + [limit],
            ).fetchall()
            return [dict(zip(META_COLUMNS, row)) for row in rows]

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
