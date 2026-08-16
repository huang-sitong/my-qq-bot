"""InMemoryMessageQueue 单元测试。"""

import asyncio

from common.queue import InMemoryMessageQueue


def test_in_memory_queue_put_get_and_qsize():
    async def run():
        q = InMemoryMessageQueue(maxsize=2)
        assert q.qsize() == 0
        await q.put("a")
        await q.put("b")
        assert q.qsize() == 2
        assert await q.get() == "a"
        q.task_done()
        assert await q.get() == "b"
        q.task_done()
        await q.join()
        assert q.qsize() == 0

    asyncio.run(run())


def test_in_memory_queue_put_nowait_get_nowait():
    q = InMemoryMessageQueue(maxsize=1)
    q.put_nowait("x")
    assert q.get_nowait() == "x"
    q.task_done()
