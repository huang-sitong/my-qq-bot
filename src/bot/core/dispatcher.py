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
from domain.bot.identity import BotIdentity
from domain.bot.index_task import IndexTurnTask
from domain.bot.message import IncomingMessage
from domain.bot.router import RouteAction, RouteDecision

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

        human = self._build_human_message(message, auto_reply=auto_reply_allowed)
        if decision.action == RouteAction.CONTEXT_ONLY:
            thread_config = {"configurable": {"thread_id": message.thread_id}}
            await self.graph.aupdate_state(thread_config, {"messages": [human]})
            await self._enqueue_index(message, "")
            return

        await self._run_reply_graph(message, human, auto_reply_allowed)

    async def dispatch_batch(
        self,
        messages: list[IncomingMessage],
        decisions: list[RouteDecision],
        *,
        auto_reply_flags: list[bool] | None = None,
    ) -> None:
        """合并投递一批同 thread 消息：整批只跑一次图、只回一条。

        突发（burst）消息合并为一次 ``graph.ainvoke``（LLM 一次看到全部
        消息、生成一条回复），或一次 ``aupdate_state``（context_only）；
        压缩检查也只做一次。RAG 索引仍按每条消息逐条入队，bot 回复挂在
        每条消息上。命令消息不经过这里——worker 已在其原位置单独执行。

        HumanMessage 携带 user_id/user_name/image_srcs/auto_reply 元数据，
        记忆、视觉与冷却语义按各自消息归属，不再依赖“最后一条消息”的标量字段。
        """
        flags = auto_reply_flags or [False] * len(messages)
        keep = [
            (m, d, flag)
            for m, d, flag in zip(messages, decisions, flags)
            if d.action in {RouteAction.REPLY, RouteAction.CONTEXT_ONLY}
        ]
        if not keep:
            return
        if self.graph is None:
            return
        first = keep[0][0]
        if self._compactor is not None:
            await self._compactor.compact_if_needed(first.thread_id)
        humans = [
            self._build_human_message(m, auto_reply=flag)
            for m, _, flag in keep
        ]
        if any(d.action == RouteAction.REPLY for _, d, _ in keep):
            await self._run_reply_graph_batch(
                [m for m, _, _ in keep],
                humans,
                auto_reply_flags=[flag for _, _, flag in keep],
            )
            return
        thread_config = {"configurable": {"thread_id": first.thread_id}}
        await self.graph.aupdate_state(thread_config, {"messages": humans})
        for m, _, _ in keep:
            await self._enqueue_index(m, "")

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

    def _build_human_message(
        self,
        message: IncomingMessage,
        *,
        auto_reply: bool = False,
    ) -> HumanMessage:
        kwargs = {
            "user_id": message.user_id,
            "user_name": message.user_name,
            "image_srcs": message.image_srcs,
        }
        kwargs["auto_reply"] = auto_reply
        return HumanMessage(
            content=message.llm_text,
            name=message.user_name or None,
            additional_kwargs=kwargs,
        )

    def _build_graph_input(
        self,
        message: IncomingMessage,
        humans: list[HumanMessage],
        auto_reply_allowed: bool,
    ) -> dict:
        """构造图输入；``humans`` 可含多条（burst 合并轮）。"""
        return {
            "thread_id": message.thread_id,
            "channel_id": message.channel_id,
            "persona": self._persona,
            "reply_text": "",
            "should_respond": True,
            "bot_name": self._identity.name,
            "bot_id": self._identity.id,
            "tool_rounds": 0,
            "channel_type": message.channel_type,
            "content_kind": message.content_kind,
            "has_text": message.has_text,
            "llm_text": message.llm_text,
            "clean_text": message.clean_text,
            "mentions": message.mentions,
            "vision_target_count": len(humans),
            "auto_reply": auto_reply_allowed,
            "messages": humans,
        }

    async def _run_reply_graph(
        self,
        message: IncomingMessage,
        human: HumanMessage,
        auto_reply_allowed: bool,
    ) -> None:
        await self._run_reply_graph_batch(
            [message], [human], [auto_reply_allowed],
        )

    async def _run_reply_graph_batch(
        self,
        messages: list[IncomingMessage],
        humans: list[HumanMessage],
        auto_reply_flags: list[bool],
    ) -> None:
        """跑一次回复图；突发合并轮整批一条回复，RAG 索引逐条入队。"""
        last = messages[-1]
        auto_reply_allowed = any(auto_reply_flags)
        recursion_limit = (
            self._bot_config.graph_recursion_limit
            if self._bot_config is not None
            else 128
        )
        try:
            result = await self.graph.ainvoke(
                self._build_graph_input(last, humans, auto_reply_allowed),
                {
                    "configurable": {"thread_id": last.thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
        except Exception:
            logger.exception(
                "Graph invoke failed: trace=%s thread=%s",
                last.trace_id, last.thread_id,
            )
            return

        reply_text = result.get("reply_text", "")
        if reply_text:
            await self._send_reply(last.channel_id, reply_text)
        if reply_text and auto_reply_allowed and self._on_auto_reply_sent is not None:
            self._on_auto_reply_sent(last.thread_id)
        for message in messages:
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
