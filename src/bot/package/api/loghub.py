"""日志实时流：tail bot 的 ``web.jsonl`` 并广播给 WebSocket 客户端。

bot 进程每次启动会重建 ``log/web.jsonl``（只保留本次运行日志）。
控制台后端从文件**开头**开始 tail；每个订阅者连接时从头读取当前文件，
因此页面一打开就能看到本次运行已产生的全部日志，之后继续实时推送新日志。

bot 与控制台同机部署（``start_all.sh``），因此基于文件的 tail 足够实时且
两者解耦：bot 不依赖控制台是否在线，控制台重启后从文件开头继续即可。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# src/bot/package/api/loghub.py -> 项目根 上溯 4 级
# （api 子包按依赖约束只允许依赖 config，故不 import utils.paths）
PROJECT_ROOT = Path(__file__).resolve().parents[4]

DEFAULT_LOG_PATH = PROJECT_ROOT / "log" / "web.jsonl"


class LogHub:
    """增量读取 ``web.jsonl`` 并广播给订阅者。

    - :meth:`start` 启动后台 tail 任务；
    - :meth:`subscribe` 注册一个订阅者队列（从文件开头开始，含本次运行历史）；
    - :meth:`unsubscribe` 注销；
    - 每个订阅者维护自己的文件偏移，读到的新日志放入其队列（满则丢最旧）。
    """

    def __init__(
        self,
        log_path: str | Path | None = None,
        *,
        poll_interval: float = 0.3,
    ) -> None:
        self.path = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH
        self.poll_interval = poll_interval
        self._subscribers: dict[asyncio.Queue[str], int] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("LogHub started tailing %s", self.path)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("LogHub stopped")

    async def subscribe(self) -> asyncio.Queue[str]:
        # 容量给大一些，保证打开页面时能把本次运行的历史日志基本完整推给前端
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10000)
        # 从文件开头开始：页面打开即可收到本次运行已产生的全部日志
        self._subscribers[queue] = 0
        # 立即读取一次，让历史日志尽快推给新订阅者
        try:
            self._read_new()
        except Exception:
            logger.exception("LogHub initial read error")
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.pop(queue, None)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._read_new()
            except Exception:
                logger.exception("LogHub tail error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                continue

    def _read_new(self) -> None:
        """按每个订阅者自己的偏移增量读取文件，逐行解析并推送。"""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            # 文件尚未创建（bot 未启动），把订阅者位置重置为开头等它出现
            for queue in list(self._subscribers):
                self._subscribers[queue] = 0
            return
        if not self._subscribers:
            return
        for queue, pos in list(self._subscribers.items()):
            if size < pos:
                # 文件被截断/重建（bot 重启），回到开头重新读本次运行日志
                pos = 0
            if size == pos:
                continue
            with self.path.open("r", encoding="utf-8") as f:
                f.seek(pos)
                data = f.read()
                new_pos = f.tell()
            self._subscribers[queue] = new_pos
            for line in data.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._broadcast(queue, entry)

    def _broadcast(self, queue: asyncio.Queue[str], entry: dict) -> None:
        payload = json.dumps(entry, ensure_ascii=False)
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # 队列满：丢弃最旧，保证实时性
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass


__all__ = ["DEFAULT_LOG_PATH", "LogHub"]
