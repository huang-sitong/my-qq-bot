"""轻量结构化日志工具。

通过 ``ContextVar`` 在消息处理链路中携带 trace_id，让同一事件的日志可以关联
检索。默认不强制 JSON，仅在日志记录中增加 ``trace_id`` 字段。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
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


class DailyFileHandler(logging.FileHandler):
    """按天切分的日志文件 handler，文件名形如 2026-08-20.log。

    启动时以当天日期创建文件，若进程跨天运行，在下一次 emit 时自动切换到
    新日期的文件，保证同一天的日志始终落在同一文件。
    """

    def __init__(self, log_dir: Path, encoding: str = "utf-8") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.current_date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = self.log_dir / f"{self.current_date_str}.log"
        super().__init__(file_path, encoding=encoding)

    def emit(self, record: logging.LogRecord) -> None:
        # 跨天检测：日期变化时切换文件
        try:
            new_date_str = datetime.now().strftime("%Y-%m-%d")
            if new_date_str != self.current_date_str:
                self.current_date_str = new_date_str
                # 关闭旧文件，指向新文件
                if self.stream:
                    self.stream.close()
                    self.stream = None  # type: ignore[assignment]
                self.baseFilename = str(self.log_dir / f"{self.current_date_str}.log")
                self.stream = self._open()
        except Exception:  # noqa: S110
            # 日期切换失败不影响日志写入
            pass
        super().emit(record)


def setup_logging(
    log_dir: str | Path = "log",
    *,
    level: int = logging.INFO,
    console: bool = True,
) -> Path:
    """初始化 bot 日志：同时输出到控制台和根目录 ``log/`` 下的按天文件。

    日志文件为 ``<项目根>/log/YYYY-MM-DD.log``，同一天的日志追加到同一文件，
    跨天自动切换（通过 ``DailyFileHandler``）。重复调用会先清空已有 handler，
    避免在测试/重载场景下重复打印。
    """
    root = logging.getLogger()
    root.setLevel(level)

    # 清空已有 handler，保证幂等
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: S110
            pass

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

    file_handler: logging.Handler = DailyFileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(TraceIdFilter())
    root.addHandler(file_handler)

    return log_path


__all__ = ["DailyFileHandler", "TraceIdFilter", "setup_logging", "trace_context"]
