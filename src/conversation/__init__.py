"""会话/消息领域（Conversation Context）。

存放消息接入、路由、Bot 状态等核心会话领域对象。
"""

from .bash import BashConfig
from .content import Attachment, MessageKind, ParsedContent
from .identity import BotIdentity
from .message import IncomingMessage
from .router import RouteAction, RouteDecision
from .state import BotState

__all__ = [
    "Attachment",
    "BashConfig",
    "BotIdentity",
    "BotState",
    "IncomingMessage",
    "MessageKind",
    "ParsedContent",
    "RouteAction",
    "RouteDecision",
]
