"""会话/消息领域（Conversation Context）。

存放消息接入、回复策略、会话与消息记录等纯会话领域对象；不依赖
LangChain / LangGraph 等基础设施框架。
"""

from .content import IMAGE_PLACEHOLDER, Attachment, MessageKind, ParsedContent
from .conversation import Conversation
from .events import ConversationTurnCompleted
from .identity import BotIdentity
from .message import IncomingMessage
from .policy import (
    DIRECT_CHANNEL_TYPE,
    NON_REPLY_KINDS,
    ReplyDecision,
    ReplyPolicy,
)
from .record import MessageRecord
from .router import RouteAction, RouteDecision
from .turn import TurnInput

__all__ = [
    "DIRECT_CHANNEL_TYPE",
    "IMAGE_PLACEHOLDER",
    "NON_REPLY_KINDS",
    "Attachment",
    "BotIdentity",
    "Conversation",
    "ConversationTurnCompleted",
    "IncomingMessage",
    "MessageKind",
    "MessageRecord",
    "ParsedContent",
    "ReplyDecision",
    "ReplyPolicy",
    "RouteAction",
    "RouteDecision",
    "TurnInput",
]
