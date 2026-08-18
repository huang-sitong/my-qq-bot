"""轻量结构化日志工具。

通过 ``ContextVar`` 在消息处理链路中携带 trace_id，让同一事件的日志可以关联
检索。默认不强制 JSON，仅在日志记录中增加 ``trace_id`` 字段。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .paths import PROJECT_ROOT

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


def setup_logging(
    log_dir: str | Path = "log",
    *,
    level: int = logging.INFO,
    log_filename: str = "bot.log",
    console: bool = True,
) -> Path:
    """初始化 bot 日志：同时输出到控制台和根目录 ``log/`` 下的文件。

    默认日志文件为 ``<项目根>/log/bot.log``。重复调用会先清空已有 handler，
    避免在测试/重载场景下重复打印。
    """
    root = logging.getLogger()
    root.setLevel(level)

    # 清空已有 handler，保证幂等
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s [trace=%(trace_id)s]"
    )

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.addFilter(TraceIdFilter())
        root.addHandler(console_handler)

    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = PROJECT_ROOT / log_path
    log_path.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path / log_filename, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(TraceIdFilter())
    root.addHandler(file_handler)

    return log_path


__all__ = ["TraceIdFilter", "setup_logging", "trace_context"]
