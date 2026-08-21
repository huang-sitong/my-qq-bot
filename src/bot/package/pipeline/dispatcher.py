"""路由决定后的执行分发器。

Dispatcher 不判断消息应走哪条流水线，只接收 ``RouteDecision`` 并调用对应
执行器：命令、图外上下文、reply graph、系统/媒体忽略。

实现 ``bot.package.domain.ports.MessageSink`` 协议（``dispatch`` /
``dispatch_batch``），供 worker 池按端口消费。
"""

import logging

from langchain_core.messages import HumanMessage

from bot.package.commands import (
    CommandContext,
    CommandRegistry,
    CommandServices,
    can_run,
    run_command,
)
from bot.package.conversation.events import ConversationTurnCompleted
from bot.package.conversation.identity import BotIdentity
from bot.package.conversation.message import IncomingMessage
from bot.package.conversation.router import RouteAction, RouteDecision
from bot.package.conversation.turn import TurnInput
from bot.package.domain.events import DomainEventBus
from bot.package.domain.ports import (
    ContextCompactorPort,
    MessageSender,
)
from bot.package.domain.repositories import ConversationRepository
from bot.package.utils import content_to_text, format_message_for_log

logger = logging.getLogger(__name__)


class MessageDispatcher:
    """按 ``RouteDecision`` 投递到具体处理流水线。"""

    def __init__(
        self,
        *,
        graph,
        persona: str,
        api_client: MessageSender,
        bot_config=None,
        command_registry: CommandRegistry | None = None,
        command_services: CommandServices | None = None,
        compactor: ContextCompactorPort | None = None,
        identity: BotIdentity | None = None,
        conversation_repository: ConversationRepository | None = None,
        event_bus: DomainEventBus | None = None,
        on_auto_reply_sent=None,
    ) -> None:
        self.graph = graph
        self._persona = persona
        self._api_client = api_client
        self._bot_config = bot_config
        self._command_registry = command_registry
        self._command_services = command_services
        self._compactor = compactor
        self._identity = identity or BotIdentity()
        self._conversation_repository = conversation_repository
        self._event_bus = event_bus
        # 跨对象回调契约：worker 池经 MessagePipeline 在启动装配时注入
        # mark_reply_sent（auto_reply 冷却记账）。公开属性，供装配根接线。
        self.on_auto_reply_sent = on_auto_reply_sent

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

        if decision.action == RouteAction.CONTEXT_ONLY:
            # context_only 追加一律走 ConversationRepository（经聚合根校验后投影）；
            # 无仓库时不写 checkpoint，仅发布事件（与无总线丢事件同一降级哲学）。
            if self._conversation_repository is not None:
                await self._conversation_repository.append_record(
                    message.to_record(),
                    auto_reply=auto_reply_allowed,
                )
            await self._publish_turn_completed([message], "")
            return

        await self._run_reply_graph(
            message,
            self._build_human_message(message, auto_reply=auto_reply_allowed),
            auto_reply_allowed,
        )

    async def dispatch_batch(
        self,
        messages: list[IncomingMessage],
        decisions: list[RouteDecision],
        *,
        auto_reply_flags: list[bool] | None = None,
    ) -> None:
        """合并投递一批同 thread 消息：整批只跑一次图、只回一条。

        突发（burst）消息合并为一次 ``graph.ainvoke``（LLM 一次看到全部
        消息、生成一条回复），context_only 经会话仓库逐条追加；压缩检查只做一次。
        命令消息不经过这里——worker 已在其原位置单独执行。

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
        if any(d.action == RouteAction.REPLY for _, d, _ in keep):
            await self._run_reply_graph_batch(
                [m for m, _, _ in keep],
                [
                    self._build_human_message(m, auto_reply=flag)
                    for m, _, flag in keep
                ],
                auto_reply_flags=[flag for _, _, flag in keep],
            )
            return
        # context_only 追加一律走 ConversationRepository；无仓库时不写 checkpoint。
        if self._conversation_repository is not None:
            for m, _, flag in keep:
                await self._conversation_repository.append_record(
                    m.to_record(),
                    auto_reply=flag,
                )
        await self._publish_turn_completed([m for m, _, _ in keep], "")

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
        human = HumanMessage(
            content=message.llm_text,
            name=message.user_name or None,
            additional_kwargs=kwargs,
        )
        logger.info(
            "Context message thread=%s: %s",
            message.thread_id,
            format_message_for_log(human),
        )
        return human

    def _build_graph_input(
        self,
        message: IncomingMessage,
        humans: list[HumanMessage],
    ) -> dict:
        """构造图输入（仅持久态字段 + 本轮 HumanMessage）。

        当轮输入（channel_type/auto_reply/vision_target_count/mentions 等）
        由 :meth:`_build_turn_input` 构造为 ``TurnInput`` 并经 run config 注入，
        不进入 BotState 持久化。``humans`` 可含多条（burst 合并轮）。
        """
        return {
            "thread_id": message.thread_id,
            "channel_id": message.channel_id,
            "persona": self._persona,
            "reply_text": "",
            "should_respond": True,
            "bot_name": self._identity.name,
            "tool_rounds": 0,
            "messages": humans,
        }

    def _build_turn_input(
        self,
        message: IncomingMessage,
        humans: list[HumanMessage],
        auto_reply_allowed: bool,
    ) -> TurnInput:
        """构造当轮输入 TurnInput（不落库，仅本轮图消费）。"""
        return TurnInput(
            channel_type=message.channel_type,
            bot_id=self._identity.id,
            auto_reply=auto_reply_allowed,
            content_kind=message.content_kind,
            has_text=message.has_text,
            llm_text=message.llm_text,
            clean_text=message.clean_text,
            vision_target_count=len(humans),
            vision_desc=[],
            mentions=message.mentions,
        )

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
        turn_input = self._build_turn_input(last, humans, auto_reply_allowed)
        try:
            result = await self.graph.ainvoke(
                self._build_graph_input(last, humans),
                {
                    "configurable": {
                        "thread_id": last.thread_id,
                        "turn_input": turn_input,
                    },
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
        if reply_text and auto_reply_allowed and self.on_auto_reply_sent is not None:
            self.on_auto_reply_sent(last.thread_id)
        await self._publish_turn_completed(messages, reply_text)

    async def _publish_turn_completed(
        self,
        messages: list[IncomingMessage],
        reply_text: str,
    ) -> None:
        """发布领域事件。"""
        if self._event_bus is None:
            return
        event = ConversationTurnCompleted(
            thread_id=messages[0].thread_id,
            messages=tuple(message.to_record() for message in messages),
            bot_id=self._identity.id,
            bot_name=self._identity.name,
            bot_reply=content_to_text(reply_text),
        )
        await self._event_bus.publish(event)

    async def _send_reply(self, channel_id: str, content: str) -> None:
        """Send reply text to the source channel via Satori HTTP API."""
        try:
            await self._api_client.send_message(channel_id, content)
        except Exception:
            logger.exception("Failed to send reply to channel %s", channel_id)
