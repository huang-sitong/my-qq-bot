"""会话消息内容分类的纯领域类型与常量。

``ParsedContent`` / ``Attachment`` / ``MessageKind`` 是各层共享的规范类型；
``IMAGE_PLACEHOLDER`` 是图片在 LLM 上下文/RAG 文本中的唯一占位符来源。
协议 XML 解析逻辑位于 ``platform.satori.content_parser``（基础设施适配器），
本模块不含 LangChain/LangGraph 依赖。
"""

from dataclasses import dataclass, field
from enum import Enum

# LLM 上下文 / RAG 索引中图片占位符的单一来源。
IMAGE_PLACEHOLDER = "[图片]"


class MessageKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"


@dataclass
class Attachment:
    type: str               # img / file / audio / video（标签名）
    name: str = ""          # 文件名（file 标签的 name 属性）
    src: str = ""           # 资源地址（已 unescape）
    start: int = 0
    end: int = 0


@dataclass
class ParsedContent:
    kind: MessageKind       # 主类型：首个媒体标签决定
    attachments: list[Attachment] = field(default_factory=list)
    clean_text: str = ""    # 剥全部标签、unescape、折叠空白（RAG 用）
    llm_text: str = ""      # 媒体→占位符、@→@昵称(id)/所有成员、剥其余（LLM 用）
    has_text: bool = False
    mentions: dict[str, str] = field(default_factory=dict)  # 顶层 @ 提及 {id: 昵称}

    @property
    def has_media(self) -> bool:
        return bool(self.attachments)
