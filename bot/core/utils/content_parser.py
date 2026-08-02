"""消息内容解析：从 Satori content 字符串分类文本/图片/文件/媒体。

LLOneBot 只发送 ``content`` 字符串（无结构化 ``elements`` 数组），图片/文件/
语音/视频都以自闭合标签嵌入字符串，例如 ``<img .../>``。本模块用正则解析这些
标签，产出消息类型（主类型）、附件列表和两种清洗文本：

- ``clean_text``：剥掉全部标签，供 RAG 索引用（纯文本）
- ``to_llm_text``：媒体标签替换为 ``[图片]`` 等占位符、剥 at 标签，供 LLM 用

类型定义（MessageKind/Attachment/ParsedContent）见 ``object.bot.content``。
"""

import html
import re
from object.bot.content import Attachment, MessageKind, ParsedContent

_MEDIA_TAG_RE = re.compile(r"<(img|file|audio|video)\b([^>]*?)/?>", re.IGNORECASE)
_AT_TAG_RE = re.compile(r"<at\b[^>]*?/?>", re.IGNORECASE)
_ALL_TAG_RE = re.compile(r"<[a-z]+\b[^>]*?/?>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*"([^"]*)"')

_PLACEHOLDERS = {
    "img": "[图片]",
    "file": "[文件]",
    "audio": "[语音]",
    "video": "[视频]",
}

_TAG_TO_KIND = {
    "img": "image",
    "file": "file",
    "audio": "audio",
    "video": "video",
}


def _parse_tag_attrs(tag_body: str) -> dict:
    return {k: html.unescape(v) for k, v in _ATTR_RE.findall(tag_body)}


def parse_attachments(content: str) -> list[Attachment]:
    """解析 content 里的媒体标签，返回附件列表。"""
    attachments = []
    for m in _MEDIA_TAG_RE.finditer(content):
        tag_type = m.group(1).lower()
        attrs = _parse_tag_attrs(m.group(2))
        attachments.append(
            Attachment(
                type=tag_type,
                name=attrs.get("name", ""),
                src=attrs.get("src", ""),
                start=m.start(),
                end=m.end(),
            )
        )
    return attachments


def _kind_from_attachments(attachments: list[Attachment]) -> MessageKind:
    if not attachments:
        return MessageKind.TEXT
    return MessageKind(_TAG_TO_KIND[attachments[0].type])


def classify_content(content: str) -> MessageKind:
    """返回消息主类型：无媒体标签为 TEXT，否则由首个媒体标签决定。"""
    return _kind_from_attachments(parse_attachments(content))


def clean_text(content: str) -> str:
    """剥掉全部元素标签（at/img/file/audio/video/...），unescape 并折叠空白。"""
    text = _ALL_TAG_RE.sub("", content)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def to_llm_text(content: str) -> str:
    """媒体标签替换为占位符、剥掉 at 标签，保留其余文本供 LLM 使用。"""
    text = _MEDIA_TAG_RE.sub(lambda m: _PLACEHOLDERS[m.group(1).lower()], content)
    text = _AT_TAG_RE.sub("", text)
    return html.unescape(text).strip()


def parse_content(content: str) -> ParsedContent:
    """一次解析产出类型、附件与两种清洗文本。"""
    attachments = parse_attachments(content)
    clean = clean_text(content)
    return ParsedContent(
        kind=_kind_from_attachments(attachments),
        attachments=attachments,
        clean_text=clean,
        llm_text=to_llm_text(content),
        has_text=bool(clean.strip()),
    )
