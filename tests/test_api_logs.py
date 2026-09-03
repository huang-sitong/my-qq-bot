"""日志实时流测试：LogHub tail 逻辑 + WS /api/logs/ws 广播。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from bot.package.api import LogHub, create_api_app


def _append(f: Path, entry: dict) -> None:
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        fh.flush()


def test_loghub_loads_current_run_and_tails_new(tmp_path: Path):
    """订阅者从头开始，能收到本次运行已产生的历史，再继续收新增。"""
    f = tmp_path / "web.jsonl"
    _append(f, {"ts": "old", "level": "INFO", "logger": "old", "message": "old", "trace_id": "-"})
    hub = LogHub(f, poll_interval=0.02)

    async def scenario() -> list[str]:
        await hub.start()
        queue = await hub.subscribe()
        _append(f, {"ts": "now", "level": "INFO", "logger": "bot", "message": "hi", "trace_id": "t1"})
        await asyncio.sleep(0.1)
        got: list[str] = []
        while not queue.empty():
            got.append(queue.get_nowait())
        await hub.stop()
        return got

    got = asyncio.run(scenario())
    assert len(got) == 2
    first = json.loads(got[0])
    assert first["message"] == "old"
    second = json.loads(got[1])
    assert second["message"] == "hi"
    assert second["level"] == "INFO"
    assert second["trace_id"] == "t1"


def test_loghub_subscriber_receives_existing_history(tmp_path: Path):
    """打开页面时，应能收到本次运行已经产生的全部历史日志。"""
    f = tmp_path / "web.jsonl"
    _append(f, {"ts": "t1", "level": "INFO", "logger": "bot", "message": "first", "trace_id": "-"})
    _append(f, {"ts": "t2", "level": "INFO", "logger": "bot", "message": "second", "trace_id": "-"})
    hub = LogHub(f, poll_interval=0.02)

    async def scenario() -> list[str]:
        await hub.start()
        queue = await hub.subscribe()
        await asyncio.sleep(0.1)
        got: list[str] = []
        while not queue.empty():
            got.append(queue.get_nowait())
        await hub.stop()
        return got

    got = asyncio.run(scenario())
    assert [json.loads(x)["message"] for x in got] == ["first", "second"]



def test_loghub_handles_missing_file(tmp_path: Path):
    """文件不存在时不崩，创建后可继续 tail。"""
    f = tmp_path / "web.jsonl"
    hub = LogHub(f, poll_interval=0.02)

    async def scenario() -> list[str]:
        await hub.start()
        queue = await hub.subscribe()
        await asyncio.sleep(0.05)
        _append(f, {"ts": "now", "level": "INFO", "logger": "bot", "message": "later", "trace_id": "-"})
        await asyncio.sleep(0.1)
        got: list[str] = []
        while not queue.empty():
            got.append(queue.get_nowait())
        await hub.stop()
        return got

    got = asyncio.run(scenario())
    assert len(got) == 1
    assert json.loads(got[0])["message"] == "later"


def test_logs_ws_streams_new_entries(tmp_path: Path):
    """WS /api/logs/ws 实时推送新增日志。"""
    f = tmp_path / "web.jsonl"
    hub = LogHub(f, poll_interval=0.02)
    app = create_api_app(log_hub=hub)

    with TestClient(app) as client, client.websocket_connect("/api/logs/ws") as ws:
        _append(f, {"ts": "now", "level": "WARNING", "logger": "bot", "message": "ws-msg", "trace_id": "t9"})
        data = ws.receive_text()
    entry = json.loads(data)
    assert entry["message"] == "ws-msg"
    assert entry["level"] == "WARNING"
    assert entry["logger"] == "bot"


def test_logs_ws_not_ready_closes():
    """LogHub 未装配（无 lifespan）时，WS 应直接关闭。"""
    app = create_api_app()
    # 不进入 lifespan（TestClient 不挂上下文管理器），log_hub 未启动
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/api/logs/ws"
    ) as ws:
        ws.receive_text()


def test_logs_ws_unsubscribes_on_idle_disconnect(tmp_path: Path):
    """空闲断开后，订阅者应从 LogHub 移除（不泄漏）。"""
    f = tmp_path / "web.jsonl"
    hub = LogHub(f, poll_interval=0.02)
    app = create_api_app(log_hub=hub)

    with TestClient(app) as client:
        with client.websocket_connect("/api/logs/ws"):
            assert len(hub._subscribers) == 1
        # 退出 websocket 上下文后，服务端应感知断开并注销
        for _ in range(50):
            if not hub._subscribers:
                break
            asyncio.run(asyncio.sleep(0.02))
        assert len(hub._subscribers) == 0
