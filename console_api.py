"""控制台后端入口：启动 FastAPI（uvicorn）。

用法：
    uv run python console_api.py                # 默认 127.0.0.1:8000
    uv run python console_api.py --port 9000
    uv run python console_api.py --host 0.0.0.0
"""

from __future__ import annotations

import argparse

import uvicorn

from bot.package.api import create_api_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ Bot 控制台后端")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--reload", action="store_true", help="开发热重载")
    args = parser.parse_args()

    app = create_api_app()
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
