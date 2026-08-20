"""会话（Conversation）纯领域聚合根。

``Conversation`` 是会话限界上下文的聚合根：会话身份、摘要、激活技能与
工具轮数的修改都通过本对象的方法收口，外部（图节点 / 命令 / 仓库适配器）
不再各自散落状态更新规则。

消息在领域侧建模为 ``MessageRecord`` 元组；LangGraph checkpoint 中的
``BaseMessage`` 只是该聚合的框架投影（见 ``orchestration/state.py`` 与
``orchestration/conversation_repository.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from bot.package.conversation.policy import ReplyDecision, ReplyPolicy

if TYPE_CHECKING:
    from bot.package.conversation.message import IncomingMessage
    from bot.package.conversation.record import MessageRecord


@dataclass(frozen=True)
class Conversation:
    """一个聊天会话（thread）聚合。

    ``thread_id`` 是应用层会话隔离键（当前为 ``platform:guild:channel``），
    ``bot_id`` / ``bot_name`` 是回复策略所需的最小 bot 身份上下文。
    """

    thread_id: str
    bot_id: str
    bot_name: str
    messages: tuple[MessageRecord, ...] = ()
    conversation_summary: str = ""
    active_skills: tuple[str, ...] = ()
    tool_rounds: int = 0

    def __post_init__(self) -> None:
        if not self.thread_id.strip():
            raise ValueError("thread_id must not be empty")
        if self.tool_rounds < 0:
            raise ValueError("tool_rounds must be >= 0")
        if any(record.thread_id != self.thread_id for record in self.messages):
            raise ValueError("all message records must belong to this conversation")

    @classmethod
    def from_message(
        cls,
        message: IncomingMessage,
        *,
        bot_id: str,
        bot_name: str,
    ) -> Conversation:
        """从一条入站消息重建其所属会话。"""
        return cls(
            thread_id=message.thread_id,
            bot_id=bot_id,
            bot_name=bot_name,
            messages=(message.to_record(),),
        )

    @classmethod
    def restore(
        cls,
        *,
        thread_id: str,
        bot_id: str = "",
        bot_name: str = "",
        messages: tuple[MessageRecord, ...] = (),
        conversation_summary: str = "",
        active_skills: tuple[str, ...] = (),
        tool_rounds: int = 0,
    ) -> Conversation:
        """从状态投影还原聚合（消息可为空；框架层负责映射 LangChain 消息）。"""
        return cls(
            thread_id=thread_id,
            bot_id=bot_id,
            bot_name=bot_name,
            messages=messages,
            conversation_summary=conversation_summary,
            active_skills=active_skills,
            tool_rounds=tool_rounds,
        )

    def decide(
        self,
        message: IncomingMessage,
        *,
        auto_reply: bool = False,
    ) -> ReplyDecision:
        """对属于本会话的消息执行回复/入上下文策略。"""
        self._ensure_message_belongs(message)
        return ReplyPolicy.evaluate(
            message,
            bot_id=self.bot_id,
            bot_name=self.bot_name,
            auto_reply=auto_reply,
        )

    def record_message(self, record: MessageRecord) -> Conversation:
        """把一条消息记入会话历史，返回新聚合快照。"""
        if record.thread_id != self.thread_id:
            raise ValueError(
                f"message thread_id {record.thread_id!r} does not belong "
                f"to conversation {self.thread_id!r}"
            )
        return replace(self, messages=self.messages + (record,))

    def record_tool_call(self) -> Conversation:
        """记录一次工具调用轮次。"""
        return replace(self, tool_rounds=self.tool_rounds + 1)

    def activate_skill(self, skill_name: str) -> Conversation:
        """激活一个技能；已激活时幂等返回自身等价快照。"""
        skill_name = skill_name.strip()
        if not skill_name:
            raise ValueError("skill_name must not be empty")
        if skill_name in self.active_skills:
            return self
        return replace(self, active_skills=self.active_skills + (skill_name,))

    def deactivate_skill(self, skill_name: str) -> Conversation:
        """释放一个技能；不存在时幂等返回自身等价快照。"""
        skill_name = skill_name.strip()
        if not skill_name:
            raise ValueError("skill_name must not be empty")
        if skill_name not in self.active_skills:
            return self
        return replace(
            self,
            active_skills=tuple(s for s in self.active_skills if s != skill_name),
        )

    def replace_summary(self, conversation_summary: str) -> Conversation:
        """替换渐进式摘要。"""
        return replace(self, conversation_summary=conversation_summary)

    def clear_context(self) -> Conversation:
        """清空会话上下文（消息/摘要/技能/轮次），保留会话身份与人设。"""
        return replace(
            self,
            messages=(),
            conversation_summary="",
            active_skills=(),
            tool_rounds=0,
        )

    def _ensure_message_belongs(self, message: IncomingMessage) -> None:
        if message.thread_id != self.thread_id:
            raise ValueError(
                f"message thread_id {message.thread_id!r} does not belong "
                f"to conversation {self.thread_id!r}"
            )


__all__ = ["Conversation"]
