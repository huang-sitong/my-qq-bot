"""日志实时 WebSocket 路由。

前端连接 ``WS /api/logs/ws`` 后，会先收到本次运行已产生的全部日志，
再实时接收新增日志。LogHub 实例挂在 ``app.state.log_hub``
（由 :func:`create_api_app` 装配）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .loghub import LogHub

router = APIRouter(prefix="/api", tags=["logs"])


@router.websocket("/logs/ws")
async def logs_ws(websocket: WebSocket) -> None:
    log_hub: LogHub | None = getattr(websocket.app.state, "log_hub", None)
    if log_hub is None:
        await websocket.close(code=1011, reason="LogHub not ready")
        return
    await websocket.accept()
    queue = await log_hub.subscribe()
    receive_task: asyncio.Task | None = None
    try:
        while True:
            # 用 receive 任务探测客户端断开，避免 queue.get() 空等时无法感知断开
            if receive_task is None or receive_task.done():
                receive_task = asyncio.create_task(websocket.receive_text())
            get_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {receive_task, get_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if get_task in done:
                payload = get_task.result()
                await websocket.send_text(payload)
                if receive_task.done():
                    await receive_task  # 客户端已断开，抛出 WebSocketDisconnect
                    break
            else:
                get_task.cancel()
                await receive_task  # 触发断开异常后退出
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if receive_task is not None and not receive_task.done():
            receive_task.cancel()
        log_hub.unsubscribe(queue)


__all__ = ["router"]
