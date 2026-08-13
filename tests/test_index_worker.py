import asyncio

from bot.core.rag.index_worker import IndexWorker
from domain import IndexTurnTask
from tests.fakes import StubRagService


def _task(text="你好", reply="收到"):
    return IndexTurnTask(
        thread_id="t1",
        user_id="u1",
        user_name="张三",
        bot_id="bot1",
        bot_name="小助手",
        user_message=text,
        bot_reply=reply,
    )


def test_index_worker_drains_enqueued_task():
    async def run():
        rag = StubRagService()
        worker = IndexWorker(rag)
        await worker.start()
        assert await worker.enqueue(_task())
        await worker.stop()
        assert rag.last_indexed == {
            "thread_id": "t1",
            "user_id": "u1",
            "user_name": "张三",
            "bot_id": "bot1",
            "bot_name": "小助手",
            "user_message": "你好",
            "bot_reply": "收到",
        }

    asyncio.run(run())


def test_index_worker_swallows_index_failure():
    class _RaisingRag:
        async def index_turn(self, **kwargs):
            raise RuntimeError("boom")

    async def run():
        worker = IndexWorker(_RaisingRag())
        await worker.start()
        assert await worker.enqueue(_task())
        await worker.stop()

    asyncio.run(run())
