"""轻量结构化日志工具。

通过 ``ContextVar`` 在消息处理链路中携带 trace_id，让同一事件的日志可以关联
检索。日志分三路输出：

- 终端（可选）：默认只打印 INFO/WARNING/ERROR；
- 按天文件 ``log/YYYY-MM-DD.log``：人类可读的 INFO 起详细日志；
- ``log/web.jsonl``：INFO 起的结构化 JSON 行，供 Web 控制台实时展示。
"""

from __future__ import annotations

import json
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


class WebConsoleHandler(logging.FileHandler):
    """把每条日志写成一行 JSON，供 Web 控制台实时展示。

    每次 bot 启动时以 ``w`` 模式重建 ``log/web.jsonl``，因此文件只保留
    **本次运行**的日志；控制台后端从文件开头开始 tail，页面打开即可看到
    本次运行已产生的全部日志。字段：

    ``ts`` 时间戳、``level`` 级别、``logger`` 来源、``message`` 消息（含异常
    堆栈）、``trace_id`` 关联 ID。
    """

    def __init__(self, log_path: Path, encoding: str = "utf-8") -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(self.log_path, mode="w", encoding=encoding)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                if self.formatter is not None:
                    message = (
                        f"{message}\n{self.formatter.formatException(record.exc_info)}"
                    )
                else:
                    message = f"{message}\n{logging.Formatter().formatException(record.exc_info)}"
            entry = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                "trace_id": getattr(record, "trace_id", "-"),
            }
            self.stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.flush()
        except Exception:
            self.handleError(record)


def setup_logging(
    log_dir: str | Path = "log",
    *,
    level: int = logging.INFO,
    console_level: int = logging.INFO,
    file_level: int = logging.INFO,
    web_level: int = logging.INFO,
    console: bool = True,
) -> Path:
    """初始化 bot 日志：三路输出，级别各自独立。

    - root 默认 ``INFO``：DEBUG 不进入任何日志输出；
    - 终端只输出 ``console_level``（默认 INFO，即 INFO/WARNING/ERROR）；
    - 按天文件 ``log/YYYY-MM-DD.log`` 输出 ``file_level``（默认 INFO）；
    - ``log/web.jsonl`` 输出 ``web_level``（默认 INFO，即 INFO/WARNING/ERROR），
      供 Web 控制台经后端 tail 后实时展示。

    重复调用会先清空已有 handler，避免在测试/重载场景下重复打印。
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
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(TraceIdFilter())
        root.addHandler(console_handler)

    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = PROJECT_ROOT / log_path
    log_path.mkdir(parents=True, exist_ok=True)

    file_handler: logging.Handler = DailyFileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(TraceIdFilter())
    root.addHandler(file_handler)

    web_handler: logging.Handler = WebConsoleHandler(
        log_path / "web.jsonl", encoding="utf-8"
    )
    web_handler.setLevel(web_level)
    web_handler.addFilter(TraceIdFilter())
    root.addHandler(web_handler)

    return log_path


__all__ = [
    "DailyFileHandler",
    "TraceIdFilter",
    "WebConsoleHandler",
    "setup_logging",
    "trace_context",
]
