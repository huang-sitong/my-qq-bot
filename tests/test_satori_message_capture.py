"""Capture LLOneBot's real Satori wire format for each message type.

Diagnostic (not a pass/fail test): connect to the Satori WebSocket and dump
the RAW ``op=0 message-created`` event body for every message the user sends.

Run manually, then send one message per type in QQ (text / image / sticker /
text+image / file / video / audio):
    uv run pytest tests/test_satori_message_capture.py -s --tb=no

Each event is printed in full and appended to ``docs/captured_messages.jsonl``.
"""

import asyncio
import json
import logging
import os
import pathlib

import pytest
import websockets

from bot.package.config import BotConfig
from bot.package.platform.satori import EventBody  # show what the current model keeps/drops

logging.basicConfig(level=logging.WARNING)

# 诊断测试：默认跳过（会连接真实 WS 并阻塞 300s），设置 BOT_CAPTURE=1 才运行。
pytestmark = pytest.mark.skipif(
    not os.getenv("BOT_CAPTURE"),
    reason="diagnostic capture needs BOT_CAPTURE=1 (hangs on real WS)",
)

# 诊断工具用进程 env（BOT_WS_URL/BOT_TOKEN），不读 .env 文件——避免真实 .env 里
# 任一非法值在模块导入时崩掉整个测试收集
_capture_config = BotConfig(_env_file=None)
WS_URL = _capture_config.ws_url
TOKEN = _capture_config.token
CAPTURE_SECONDS = int(os.getenv("BOT_CAPTURE_SECONDS", "300"))
OUT_FILE = pathlib.Path(__file__).resolve().parents[1] / "docs" / "captured_messages.jsonl"


def _analyze(body: dict) -> str:
    msg = body.get("message") or {}
    content = msg.get("content")
    elements = msg.get("elements") or []
    kinds = [e.get("type") for e in elements] if isinstance(elements, list) else []
    return f"content={content!r} element_types={kinds}"


async def _recv_loop(ws) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CAPTURE_SECONDS
    count = 0
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=CAPTURE_SECONDS)
        except (TimeoutError, websockets.ConnectionClosed):
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        op = data.get("op")
        if op == 1:  # PING -> PONG
            await ws.send(json.dumps({"op": 2}))
        elif op == 4:
            print("login:", json.dumps(data.get("body"), ensure_ascii=False))
        elif op == 0:
            body = data.get("body") or {}
            if body.get("type") == "message-created":
                count += 1
                OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
                with OUT_FILE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(body, ensure_ascii=False) + "\n")
                print("=" * 80)
                print("ANALYSIS:", _analyze(body))
                print(json.dumps(body, ensure_ascii=False, indent=2))
                try:
                    current = EventBody.model_validate(body)
                    kept = current.message.model_dump() if current.message else None
                    print("CURRENT MODEL keeps:",
                          json.dumps(kept, ensure_ascii=False, indent=2))
                except Exception as exc:
                    print("CURRENT MODEL parse error:", exc)
    print(f"done — captured {count} message(s) -> {OUT_FILE}")


async def _capture() -> None:
    identify = {"op": 3}
    if TOKEN:
        identify["token"] = TOKEN
    print(f"connecting {WS_URL} …")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps(identify))
        print(f"IDENTIFY sent — capturing {CAPTURE_SECONDS}s, send test messages now")
        await _recv_loop(ws)


class TestSatoriMessageCapture:
    def test_capture_wire_format(self):
        asyncio.run(_capture())
