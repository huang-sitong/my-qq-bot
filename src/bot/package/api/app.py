"""FastAPI 应用工厂：挂载设置/日志路由并开启 CORS（供 Vue 开发服务器跨域）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .loghub import LogHub
from .logs import router as logs_router
from .router import router

# Vue 开发服务器默认端口；也可经 create_api_app(cors_origins=...) 覆盖
DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_api_app(
    *,
    cors_origins: list[str] | None = None,
    title: str = "QQ Bot Console API",
    version: str = "0.1.0",
    log_hub: LogHub | None = None,
) -> FastAPI:
    """装配控制台后端。

    ``log_hub`` 可注入自定义 LogHub（测试指向临时文件）；默认从项目
    ``log/web.jsonl`` 末尾开始 tail。
    """
    hub = log_hub if log_hub is not None else LogHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.log_hub = hub
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(title=title, version=version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or DEFAULT_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(logs_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


__all__ = ["create_api_app"]
