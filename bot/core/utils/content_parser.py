"""消息内容解析：从 Satori content 字符串分类文本/图片/文件/媒体。

LLOneBot 只发送 ``content`` 字符串（无结构化 ``elements`` 数组），图片/文件/
语音/视频都以自闭合标签嵌入字符串，例如 ``<img .../>``。本模块用正则解析这些
标签，产出消息类型（主类型）、附件列表和两种清洗文本：

- ``clean_text``：剥掉全部标签（含闭合/注释），供 RAG 索引用（纯文本）
- ``to_llm_text``：媒体→``[图片]`` 等占位符、@→``@昵称(id)``/``所有成员``、链接→``内容 (href)``、其余标签全剥，供 LLM 用（注：``<a@b.com>``/``<https://...>`` 等非元素尖括号序列同样被剥除）
- ``parse_mentions``：只数顶层 at 提及 ``{昵称: id}``（引用/转发子树不计），供路由判定用

类型定义（MessageKind/Attachment/ParsedContent）见 ``object.bot.content``。
"""

import html
import re
from object.bot.content import Attachment, MessageKind, ParsedContent

_MEDIA_TAG_RE = re.compile(r"<(img|file|audio|video)\b([^>]*?)/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"</?[a-z]+\b[^>]*?/?>", re.IGNORECASE)   # 起始/闭合/自闭合
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)             # 注释（message.md 语法）
_LINK_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')  # 单/双引号版
_AT_TAG_RE = re.compile(r"<at\b([^>]*?)/?>", re.IGNORECASE)               # 提取/渲染 at
_CONTAINER_TAG_RE = re.compile(r"</?(quote|message)\b[^>]*/?>", re.IGNORECASE)

_PLACEHOLDERS = {
    "img": "[图片]",
    "file": "[文件]",
    "audio": "[语音]",
    "video": "[视频]",
}

# [图片] 占位符单一来源（describe_image 原位替换引用，避免魔数重复）
IMAGE_PLACEHOLDER = _PLACEHOLDERS["img"]

_TAG_TO_KIND = {
    "img": "image",
    "file": "file",
    "audio": "audio",
    "video": "video",
}


def _parse_tag_attrs(tag_body: str) -> dict:
    attrs = {}
    for m in _ATTR_RE.finditer(tag_body):
        value = m.group(2) if m.group(2) is not None else m.group(3)
        attrs[m.group(1)] = html.unescape(value)
    return attrs


def parse_attachments(content: str) -> list[Attachment]:
    """解析 content 里的媒体标签，返回附件列表。"""
    attachments = []
    for m in _MEDIA_TAG_RE.finditer(content):
        tag_type = m.group(1).lower()
        attrs = _parse_tag_attrs(m.group(2))
        attachments.append(
            Attachment(
                type=tag_type,
                name=attrs.get("name") or attrs.get("title", ""),
                src=attrs.get("src", ""),
                start=m.start(),
                end=m.end(),
            )
        )
    return attachments


def _top_level_text(content: str) -> str:
    """剥掉 quote/message 子树，返回仅含顶层文本的区域（供 parse_mentions 用）。"""
    out = []
    prev = 0
    depth = 0
    for m in _CONTAINER_TAG_RE.finditer(content):
        tag = m.group(0)
        if depth == 0:
            out.append(content[prev:m.start()])   # 只收 depth==0 的文本
        if tag.startswith("</"):
            depth = max(0, depth - 1)
        elif tag.endswith("/>"):
            pass                                   # 自闭合无子元素
        else:
            depth += 1
        prev = m.end()
    out.append(content[prev:])
    return "".join(out)


def parse_mentions(content: str) -> dict[str, str]:
    """返回顶层 at 提及 {昵称: id}；引用/转发子树不计；type=all/here 跳过。"""
    mentions = {}
    for m in _AT_TAG_RE.finditer(_top_level_text(content)):
        attrs = _parse_tag_attrs(m.group(1))
        if attrs.get("type"):      # type=all/here：非用户提及，不计
            continue
        uid = attrs.get("id", "")
        if not uid:                # 无 id 不算（type-only / 空 at）
            continue
        name = attrs.get("name") or uid   # name 可缺失 → 用 id 当 key
        mentions[name] = uid
    return mentions


def _kind_from_attachments(attachments: list[Attachment]) -> MessageKind:
    if not attachments:
        return MessageKind.TEXT
    return MessageKind(_TAG_TO_KIND[attachments[0].type])


def classify_content(content: str) -> MessageKind:
    """返回消息主类型：无媒体标签为 TEXT，否则由首个媒体标签决定。"""
    return _kind_from_attachments(parse_attachments(content))


def clean_text(content: str) -> str:
    """剥掉全部元素标签与注释（含闭合标签），unescape 并折叠空白。"""
    text = _COMMENT_RE.sub("", content)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _render_link(m: re.Match) -> str:
    """链接按 Satori 无平台支持时的建议渲染 content (href)。"""
    inner = m.group(2).strip()
    url = _parse_tag_attrs(m.group(1)).get("href", "")
    if not url:
        return inner
    return f"{inner} ({url})" if inner else url


def _render_at(m: re.Match) -> str:
    """at 渲染：@昵称(id)/@id；type=all→所有成员、here→在线成员。"""
    attrs = _parse_tag_attrs(m.group(1))
    at_type = attrs.get("type", "")
    if at_type == "all":
        return "所有成员"
    if at_type == "here":
        return "在线成员"
    uid = attrs.get("id", "")
    if not uid:
        return ""
    name = attrs.get("name")
    if name:
        return f"@{name}({uid})"
    return f"@{uid}"


def to_llm_text(content: str) -> str:
    """媒体→占位符、@→@昵称(id)/所有成员、链接→content (href)、其余标签全剥。"""
    text = _COMMENT_RE.sub("", content)
    text = _MEDIA_TAG_RE.sub(lambda m: _PLACEHOLDERS[m.group(1).lower()], text)
    text = _AT_TAG_RE.sub(_render_at, text)
    text = _LINK_RE.sub(_render_link, text)
    text = _TAG_RE.sub("", text)
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
        mentions=parse_mentions(content),
    )
