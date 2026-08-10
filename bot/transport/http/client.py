import logging

import httpx
from pydantic import BaseModel

from common import BotConfig
from object.satori.api import MESSAGE_CREATE, Endpoint, MessageCreateParams

logger = logging.getLogger(__name__)


class SatoriApiClient:

    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._user_id: str | None = None
        self._http: httpx.AsyncClient | None = None

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

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
