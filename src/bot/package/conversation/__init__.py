"""会话/消息领域（Conversation Context）。

存放消息接入、路由、Bot 状态等核心会话领域对象。
"""

from .content import Attachment, MessageKind, ParsedContent
from .identity import BotIdentity
from .message import IncomingMessage
from .router import RouteAction, RouteDecision
from .state import BotState
from .turn import TurnInput

__all__ = [
    "Attachment",
    "BotIdentity",
    "BotState",
    "IncomingMessage",
    "MessageKind",
    "ParsedContent",
    "RouteAction",
    "RouteDecision",
    "TurnInput",
]
