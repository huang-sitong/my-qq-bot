import logging
from pathlib import Path
from xml.sax.saxutils import quoteattr

import httpx
from pydantic import BaseModel

from bot.package.config import BotConfig
from bot.package.platform.satori.api import MESSAGE_CREATE, Endpoint, MessageCreateParams

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}


class SatoriApiClient:

    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._user_id: str | None = None
        self._http: httpx.AsyncClient | None = None
        self._onebot11_http: httpx.AsyncClient | None = None

    @property
    def config(self) -> BotConfig:
        return self._config

    def set_user_id(self, user_id: str | None) -> None:
        """Set the runtime Satori user id used for API request headers."""
        self._user_id = user_id

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self._config.api_base_url)
        return self._http

    @property
    def onebot11_http(self) -> httpx.AsyncClient:
        if self._onebot11_http is None:
            self._onebot11_http = httpx.AsyncClient(
                base_url=self._config.onebot11_api_base_url,
                timeout=httpx.Timeout(
                    self._config.onebot11_timeout, connect=10,
                ),
            )
        return self._onebot11_http

    async def call_api(self, endpoint: Endpoint, params: BaseModel | None = None, **extra) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._config.api_platform:
            headers["Satori-Platform"] = self._config.api_platform
        if self._user_id:
            headers["Satori-User-ID"] = self._user_id
        if self._config.token:
            headers["Authorization"] = f"Bearer {self._config.token}"

        payload: dict = {}
        if params is not None:
            payload.update(params.model_dump(exclude_none=True))
        payload.update(extra)

        resp = await self.http.post(endpoint.path, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def send_message(self, channel_id: str, content: str) -> dict:
        params = MessageCreateParams(channel_id=channel_id, content=content)
        return await self.call_api(MESSAGE_CREATE, params)

    async def send_file(self, channel_id: str, path: str, name: str | None = None) -> dict:
        """发送本地文件到 Satori 频道。

        LLBot 的 Satori ``<file>`` 元素尚未真正发送文件，因此普通文件直接走
        OneBot11 HTTP 上传动作；图片走标准 ``message.create`` 图片消息。
        """
        local = Path(path).expanduser().resolve()
        if not local.is_file():
            raise ValueError(f"文件不存在或不是文件：{local}")
        final_name = (name or "").strip() or local.name
        if "/" in final_name or "\\" in final_name:
            raise ValueError("文件名不能包含 / 或 \\")

        if local.suffix.lower() in _IMAGE_EXTENSIONS:
            content = (
                f"<img src={quoteattr(local.as_uri())} "
                f"title={quoteattr(final_name)}/>"
            )
            return await self.send_message(channel_id, content)

        if channel_id.startswith("private:"):
            user_id = channel_id.split(":", 1)[1]
            if not user_id:
                raise ValueError("私聊频道缺少用户 ID")
            return await self._call_onebot11_action("upload_private_file", {
                "user_id": user_id,
                "file": str(local),
                "name": final_name,
            })
        return await self._call_onebot11_action("upload_group_file", {
            "group_id": channel_id,
            "file": str(local),
            "name": final_name,
        })

    async def _call_onebot11_action(self, action: str, params: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._config.token:
            headers["Authorization"] = f"Bearer {self._config.token}"
        resp = await self.onebot11_http.post(f"/{action}", json=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._onebot11_http is not None:
            await self._onebot11_http.aclose()
            self._onebot11_http = None
