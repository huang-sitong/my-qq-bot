import asyncio
import logging

from bot.package.domain import IndexTurnTask
from bot.package.utils.logging import trace_context

logger = logging.getLogger(__name__)


class IndexWorker:
    def __init__(self, rag_service, maxsize: int = 1000):
        self._rag_service = rag_service
        self._queue: asyncio.Queue[IndexTurnTask | None] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task | None = None
        self._stopped = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def enqueue(self, task: IndexTurnTask) -> bool:
        if self._stopped:
            return False
        try:
            self._queue.put_nowait(task)
            return True
        except asyncio.QueueFull:
            logger.warning("RAG index queue full; dropping task for thread %s", task.thread_id)
            return False

    async def _run(self) -> None:
        while True:
            task = await self._queue.get()
            try:
                if task is None:
                    return
                with trace_context(task.trace_id):
                    await self._rag_service.index_turn(
                        thread_id=task.thread_id,
                        user_id=task.user_id,
                        user_name=task.user_name,
                        bot_id=task.bot_id,
                        bot_name=task.bot_name,
                        user_message=task.user_message,
                        bot_reply=task.bot_reply,
                    )
            except Exception:
                logger.exception("RAG index task failed for thread %s", task.thread_id)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopped = True
        await self._queue.put(None)
        await self._task
