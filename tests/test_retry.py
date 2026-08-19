"""retry_async 单元测试。"""

import asyncio

import pytest

from bot.package.utils.retry import retry_async


def test_retry_async_success_without_retry():
    async def run():
        calls = 0

        async def ok():
            nonlocal calls
            calls += 1
            return "ok"

        result = await retry_async(ok, retries=2)
        assert result == "ok"
        assert calls == 1

    asyncio.run(run())


def test_retry_async_succeeds_after_retries():
    async def run():
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("boom")
            return "recovered"

        result = await retry_async(flaky, retries=3, base_delay=0)
        assert result == "recovered"
        assert calls == 3

    asyncio.run(run())


def test_retry_async_raises_after_exhausted():
    async def run():
        calls = 0

        async def always_fail():
            nonlocal calls
            calls += 1
            raise ValueError("bad")

        with pytest.raises(ValueError, match="bad"):
            await retry_async(always_fail, retries=2, base_delay=0)
        assert calls == 3

    asyncio.run(run())
