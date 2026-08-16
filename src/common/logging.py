"""轻量结构化日志工具。

通过 ``ContextVar`` 在消息处理链路中携带 trace_id，让同一事件的日志可以关联
检索。默认不强制 JSON，仅在日志记录中增加 ``trace_id`` 字段。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


class TraceIdFilter(logging.Filter):
    """把当前 ContextVar 中的 trace_id 注入 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_var.get() or "-"
        return True


@contextmanager
def trace_context(trace_id: str) -> Iterator[None]:
    """在 with 块内设置 trace_id，退出后恢复原值。"""
    token = _trace_id_var.set(trace_id)
    try:
        yield
    finally:
        _trace_id_var.reset(token)
