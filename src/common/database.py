"""数据库目录与生命周期管理。

各存储服务（checkpoint / memory / milvus / embed_cache）仍由各自实现负责连接，
这里统一管理数据库根目录的创建与关闭钩子，避免 main.py 散落路径逻辑。
"""

from __future__ import annotations

import os
from pathlib import Path


class DatabaseManager:
    """管理本地数据库目录及统一路径入口。"""

    def __init__(self, db_dir: str) -> None:
        self.db_dir = db_dir

    @property
    def checkpoint_path(self) -> str:
        return os.path.join(self.db_dir, "checkpoint.sqlite")

    @property
    def memory_path(self) -> str:
        return os.path.join(self.db_dir, "memory.sqlite")

    @property
    def embed_cache_path(self) -> str:
        return os.path.join(self.db_dir, "embed_cache.sqlite")

    @property
    def milvus_uri(self) -> str:
        return os.path.join(self.db_dir, "milvus.db")

    def ensure_ready(self) -> None:
        Path(self.db_dir).mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        """统一关闭钩子。

        当前各存储服务自行管理连接；这里保留扩展点，后续可统一等待/关闭。
        """
