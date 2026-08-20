"""Satori 平台适配器门面。

把 Satori 的 WebSocket 事件源、HTTP 发送客户端、事件归一化和消息流水线绑定为
一个平台实现。``bot.package.core.app`` 只依赖此门面，不直接接触 Satori 协议细节。
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from bot.package.conversation.identity import BotIdentity
from bot.package.platform.satori import EventBody, LoginList, SatoriApiClient
from bot.package.platform.satori.ingress import SatoriMessageIngress
from bot.package.platform.satori.websocket import SatoriClient

if TYPE_CHECKING:
    from bot.package.pipeline.pipeline import MessagePipeline

logger = logging.getLogger(__name__)


class SatoriAdapter:
    """Satori 平台适配器：事件注册 + 身份维护 + 生命周期。"""

    def __init__(
        self,
        client: SatoriClient,
        api_client: SatoriApiClient,
        *,
        pipeline: MessagePipeline | None = None,
        command_services=None,
        identity: BotIdentity | None = None,
    ) -> None:
        self.client = client
        self.api_client = api_client
        self.pipeline = pipeline
        self.command_services = command_services
        self.identity = identity or BotIdentity()
        self.ingress = SatoriMessageIngress()

    def bind_pipeline(self, pipeline: MessagePipeline) -> None:
        self.pipeline = pipeline

    def register_handlers(self) -> None:
        self.client.on("message-created")(self._on_message)
        self.client.on("login")(self.handle_login)

    async def _on_message(self, event: EventBody) -> None:
        if self.pipeline is None:
            logger.warning("Satori message dropped: pipeline not bound")
            return
        message = self.ingress.normalize(event)
        if message is None:
            return
        await self.pipeline.enqueue(message)

    async def handle_login(self, login_list: LoginList) -> None:
        """从 login 事件提取机器人身份并同步到 API client。"""
        logins = login_list.logins
        if not logins:
            return
        user = logins[0].user
        if user is None:
            return
        self.identity.id = user.id
        self.identity.name = user.name or user.nick or user.id
        self.api_client.set_user_id(self.identity.id)
        if self.command_services is not None:
            self.command_services.bot_name = self.identity.name
        logger.info(
            "Bot info set: id=%s name=%s", self.identity.id, self.identity.name,
        )

    async def run(self) -> None:
        await self.client.run()

    async def close(self) -> None:
        await self.client.disconnect()
        await self.api_client.close()


__all__ = ["SatoriAdapter"]
