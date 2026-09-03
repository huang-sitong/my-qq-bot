"""FastAPI 应用工厂：挂载设置路由并开启 CORS（供 Vue 开发服务器跨域）。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
) -> FastAPI:
    app = FastAPI(title=title, version=version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or DEFAULT_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


__all__ = ["create_api_app"]
