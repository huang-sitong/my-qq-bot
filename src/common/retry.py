"""轻量异步重试工具。

用于幂等/只读外部调用（如图片下载、视觉生成、嵌入查询）。发送类副作用操作
（如发送消息）不应盲目重试，避免重复投递。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


async def retry_async[T, **P](
    func: Callable[P, Awaitable[T]],
    *args: P.args,
    retries: int = 2,
    base_delay: float = 0.5,
    logger: logging.Logger | None = None,
    **kwargs: P.kwargs,
) -> T:
    """执行 ``func``，失败时按指数退避重试，最多 ``retries`` 次重试。"""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= retries:
                break
            delay = base_delay * (2**attempt)
            if logger is not None:
                logger.warning(
                    "Retry %d/%d after %.2fs: %s",
                    attempt + 1, retries, delay, type(exc).__name__,
                )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc

__all__ = ["retry_async"]
