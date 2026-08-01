"""describe_image — 图片回复路径的视觉理解节点。

对图片消息调用 Ollama 视觉模型生成描述，把 HumanMessage 里的 [图片] 占位符
原位替换为 [图片：描述]，并把描述写入 vision_desc 供 RAG 索引。
视觉服务为 None 或非图片消息时 no-op（占位符保留，行为同旧版）。
"""

import logging

from langchain_core.messages import HumanMessage

from bot.core.vision.service import VisionService
from object.bot.state import BotState

logger = logging.getLogger(__name__)


def replace_placeholders(content: str, descriptions: list[str]) -> str:
    """把 content 里每个 [图片] 按序替换成 [图片：描述]；描述为空则保留 [图片]。"""
    marker = "[图片]"
    parts = []
    idx = 0
    for desc in descriptions:
        pos = content.find(marker, idx)
        if pos == -1:
            break
        parts.append(content[idx:pos])
        parts.append(f"[图片：{desc}]" if desc else marker)
        idx = pos + len(marker)
    parts.append(content[idx:])
    return "".join(parts)


async def describe_image_node(state: BotState, vision_service: VisionService | None) -> dict:
    """为图片消息生成描述并注入消息内容。失败时降级为 [图片] 占位符。"""
    if vision_service is None:
        return {}
    image_srcs = state.get("image_srcs") or []
    if not image_srcs:
        return {}
    messages = state.get("messages") or []
    if not messages or not isinstance(messages[-1], HumanMessage):
        return {}
    msg = messages[-1]
    descriptions = await vision_service.describe_many(image_srcs)
    new_content = replace_placeholders(msg.content, descriptions)
    vision_desc = "；".join(d for d in descriptions if d)
    if new_content == msg.content and not vision_desc:
        return {"vision_desc": ""}  # 图片轮全失败：清空陈旧 vision_desc，防跨轮污染 RAG 索引
    return {
        "messages": [HumanMessage(content=new_content, id=msg.id)],  # 同 id → 原位替换
        "vision_desc": vision_desc,
    }
