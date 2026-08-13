"""路由决定后的执行分发器。

Dispatcher 不判断消息应走哪条流水线，只接收 ``RouteDecision`` 并调用对应
执行器：命令、图外上下文、reply graph、系统/媒体忽略。
"""

import logging

from langchain_core.messages import HumanMessage

from bot.core.commands import (
    CommandContext,
    CommandRegistry,
    CommandServices,
    can_run,
    run_command,
)
from bot.core.compaction import ContextCompactor
from bot.core.rag.index_worker import IndexWorker
from bot.core.utils import IMAGE_PLACEHOLDER, MessageKind, content_to_text
from bot.transport.http.client import SatoriApiClient
from object.bot.identity import BotIdentity
from object.bot.index_task import IndexTurnTask
from object.bot.message import IncomingMessage
from object.bot.router import RouteAction, RouteDecision
from object.satori import ChannelType

logger = logging.getLogger(__name__)


class MessageDispatcher:
    """按 ``RouteDecision`` 投递到具体处理流水线。"""

    def __init__(
        self,
        *,
        graph,
        persona: str,
        api_client: SatoriApiClient,
        bot_config=None,
        command_registry: CommandRegistry | None = None,
        command_services: CommandServices | None = None,
        compactor: ContextCompactor | None = None,
        index_worker: IndexWorker | None = None,
        identity: BotIdentity | None = None,
        on_auto_reply_sent=None,
    ) -> None:
        self.graph = graph
        self._persona = persona
        self._api_client = api_client
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._command_services = command_services
        self._compactor = compactor
        self._index_worker = index_worker
        self._identity = identity or BotIdentity()
        self._on_auto_reply_sent = on_auto_reply_sent

    async def dispatch(
        self,
        message: IncomingMessage,
        decision: RouteDecision,
        *,
        auto_reply_allowed: bool = False,
    ) -> None:
        if decision.action == RouteAction.COMMAND:
            await self._execute_command(message, decision)
            return
        if decision.action in {
            RouteAction.IGNORE,
            RouteAction.SYSTEM,
            RouteAction.MEDIA,
        }:
            logger.debug(
                "%s event ignored: trace=%s thread=%s",
                decision.action.value, message.trace_id, message.thread_id,
            )
            return
        if self.graph is None:
            return
        if self._compactor is not None:
            await self._compactor.compact_if_needed(message.thread_id)

        human = self._build_human_message(message)
        if decision.action == RouteAction.CONTEXT_ONLY:
            thread_config = {"configurable": {"thread_id": message.thread_id}}
            await self.graph.aupdate_state(thread_config, {"messages": [human]})
            await self._enqueue_index(message, "")
            return

        await self._run_reply_graph(message, human, auto_reply_allowed)

    async def _execute_command(
        self,
        message: IncomingMessage,
        decision: RouteDecision,
    ) -> None:
        command = decision.command
        actor = decision.actor
        if command is None or actor is None:
            return
        if not can_run(command, actor):
            reply_text = "无权执行该指令。"
        elif decision.parsed_command is not None and decision.parsed_command.error:
            reply_text = f"指令参数错误，用法：{command.usage}"
        else:
            ctx = CommandContext(
                raw=message.raw_content,
                actor=actor,
                platform=message.platform,
                guild_id=message.guild_id,
                channel_id=message.channel_id,
                thread_id=message.thread_id,
                channel_type=message.channel_type,
                args=decision.parsed_command.args if decision.parsed_command else (),
                config=self._bot_config,
                services=self._command_services,
            )
            reply_text = (await run_command(command, ctx)).text
        logger.info(
            "Command /%s by %s (admin=%s, thread=%s)",
            command.name, message.user_id, actor.is_admin, message.thread_id,
        )
        if reply_text:
            await self._send_reply(message.channel_id, reply_text)

    def _build_human_message(self, message: IncomingMessage) -> HumanMessage:
        if message.channel_type != ChannelType.DIRECT and message.user_name:
            return HumanMessage(content=message.llm_text, name=message.user_name)
        return HumanMessage(content=message.llm_text)

    def _build_graph_input(
        self,
        message: IncomingMessage,
        human: HumanMessage,
        auto_reply_allowed: bool,
    ) -> dict:
        return {
            "thread_id": message.thread_id,
            "channel_id": message.channel_id,
            "persona": self._persona,
            "reply_text": "",
            "should_respond": True,
            "bot_name": self._identity.name,
            "bot_id": self._identity.id,
            "tool_rounds": 0,
            "user_id": message.user_id,
            "channel_type": message.channel_type,
            "user_name": message.user_name,
            "content_kind": message.content_kind,
            "has_text": message.has_text,
            "llm_text": message.llm_text,
            "clean_text": message.clean_text,
            "mentions": message.mentions,
            "image_srcs": message.image_srcs,
            "auto_reply": auto_reply_allowed,
            "messages": [human],
        }

    async def _run_reply_graph(
        self,
        message: IncomingMessage,
        human: HumanMessage,
        auto_reply_allowed: bool,
    ) -> None:
        max_rounds = (
            self._bot_config.rag_max_agent_rounds
            if self._bot_config is not None
            else 3
        )
        recursion_limit = 2 * max_rounds + 8
        try:
            result = await self.graph.ainvoke(
                self._build_graph_input(message, human, auto_reply_allowed),
                {
                    "configurable": {"thread_id": message.thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
        except Exception:
            logger.exception(
                "Graph invoke failed: trace=%s thread=%s",
                message.trace_id, message.thread_id,
            )
            return

        reply_text = result.get("reply_text", "")
        if reply_text:
            await self._send_reply(message.channel_id, reply_text)
        if reply_text and auto_reply_allowed and self._on_auto_reply_sent is not None:
            self._on_auto_reply_sent(message.thread_id)
        await self._enqueue_index(message, reply_text)

    async def _enqueue_index(self, message: IncomingMessage, reply_text: str) -> None:
        if self._index_worker is None:
            return
        task = self._build_index_task(message, reply_text)
        if task is None:
            return
        await self._index_worker.enqueue(task)

    def _build_index_task(
        self,
        message: IncomingMessage,
        reply_text: str,
    ) -> IndexTurnTask | None:
        user_message = message.clean_text
        reply_text = content_to_text(reply_text).strip()
        if (
            message.content_kind == MessageKind.IMAGE.value
            and (user_message.strip() or reply_text)
        ):
            user_message = f"{user_message} {IMAGE_PLACEHOLDER}".strip()
        if not user_message.strip() and not reply_text:
            return None
        return IndexTurnTask(
            thread_id=message.thread_id,
            user_id=message.user_id,
            user_name=message.user_name,
            bot_id=self._identity.id,
            bot_name=self._identity.name,
            user_message=user_message,
            bot_reply=reply_text,
        )

    async def _send_reply(self, channel_id: str, content: str) -> None:
        """Send reply text to the source channel via Satori HTTP API."""
        try:
            await self._api_client.send_message(channel_id, content)
        except Exception:
            logger.exception("Failed to send reply to channel %s", channel_id)
