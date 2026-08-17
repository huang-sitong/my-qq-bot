"""Satori 消息内容分类的领域类型（纯数据对象，零逻辑，供各层共享引用）。

由 ``context.utils.content_parser`` 的解析函数消费；解析逻辑留在 utils，
这里只存放可被 ``domain/`` 层（含 ``BotState``）引用的规范类型。
"""

from dataclasses import dataclass, field
from enum import Enum


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
