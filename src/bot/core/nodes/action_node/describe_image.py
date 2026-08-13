"""describe_image — 图片回复路径的视觉节点。

双模式：
- ``llm_multimodal=True``：主 LLM 直接看图。图片下载成 data URL，把
  HumanMessage 的 [图片] 占位符原位替换为多模态 content 数组（图片块交错插入）；
  本地视觉（vision_service 非空）仅产出 ``vision_desc`` 供 RAG 索引，理解归主 LLM。
- ``llm_multimodal=False``（默认）：现状——本地视觉生成描述，把 [图片] 原位替换
  为 [图片：描述]，``vision_desc`` 供 RAG 索引。

视觉服务为 None 或非图片消息时 no-op（占位符保留，行为同旧版）。
``auto_reply=True`` 时跳过本地视觉：多模态主 LLM 直接收图；非多模态只保留
``[图片]`` 占位符，且不产生 ``vision_desc``。
"""

from langchain_core.messages import HumanMessage

from bot.core.utils import IMAGE_PLACEHOLDER
from bot.core.vision.service import VisionService, download_images_as_data_urls
from domain.bot.state import BotState


def replace_placeholders(content: str, descriptions: list[str]) -> str:
    """把 content 里每个 [图片] 按序替换成 [图片：描述]；描述为空则保留 [图片]。"""
    marker = IMAGE_PLACEHOLDER
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


def build_multimodal_content(text: str, data_urls: list[str]) -> list[dict]:
    """把 content 里的 [图片] 占位符按序替换为多模态 content 数组。

    文本块与 image_url 块交错插入，占位符与图片一一对应；占位符数量不足时
    多余图片追加到末尾；无占位符时图片整体追加到文本后。返回 OpenAI 兼容
    content 数组（``[{"type": "text"...}, {"type": "image_url"...}]``）。
    """
    if not data_urls:
        return [{"type": "text", "text": text}]
    marker = IMAGE_PLACEHOLDER
    blocks: list[dict] = []
    idx = 0
    used = 0
    for url in data_urls:
        pos = text.find(marker, idx)
        if pos == -1:
            break
        if pos > idx:
            blocks.append({"type": "text", "text": text[idx:pos]})
        blocks.append({"type": "image_url", "image_url": {"url": url}})
        idx = pos + len(marker)
        used += 1
    tail = text[idx:]
    if tail:
        blocks.append({"type": "text", "text": tail})
    for url in data_urls[used:]:
        blocks.append({"type": "image_url", "image_url": {"url": url}})
    return blocks


async def describe_image_node(
    state: BotState,
    vision_service: VisionService | None = None,
    llm_multimodal: bool = False,
    max_images: int = 3,
    timeout: float = 60.0,
) -> dict:
    """图片消息处理：多模态主 LLM 直接收图；纯文本 LLM 走本地视觉描述。

    失败时降级为 [图片] 占位符（多模态全下载失败 → 文本只留占位符）。
    """
    image_srcs = state.get("image_srcs") or []
    if not image_srcs:
        return {}
    messages = state.get("messages") or []
    if not messages or not isinstance(messages[-1], HumanMessage):
        return {}
    msg = messages[-1]
    auto_reply = state.get("auto_reply", False)

    # auto_reply 图片轮不走本地视觉模型：多模态由主 LLM 直接看图，
    # 非多模态只保留占位符，并清空陈旧 vision_desc 防止污染 RAG 索引。
    if auto_reply and not llm_multimodal:
        return {"vision_desc": ""}

    if llm_multimodal:
        return await _describe_multimodal(
            msg, image_srcs, vision_service, max_images, timeout,
            use_local_vision=not auto_reply,
        )

    # 纯文本模式（现状）：本地视觉描述 → [图片：描述] 原位替换
    if vision_service is None:
        return {}
    descriptions = await vision_service.describe_many(image_srcs)
    new_content = replace_placeholders(msg.content, descriptions)
    vision_desc = "；".join(d for d in descriptions if d)
    if new_content == msg.content and not vision_desc:
        return {"vision_desc": ""}  # 图片轮全失败：清空陈旧 vision_desc，防跨轮污染 RAG 索引
    return {
        "messages": [HumanMessage(content=new_content, id=msg.id)],
        "vision_desc": vision_desc,
    }


async def _describe_multimodal(
    msg: HumanMessage,
    image_srcs: list[str],
    vision_service: VisionService | None,
    max_images: int,
    timeout: float,
    use_local_vision: bool = True,
) -> dict:
    """多模态模式：下载图片 → 原位替换为 content 数组；本地视觉仅产 vision_desc。"""
    data_urls = await download_images_as_data_urls(
        image_srcs, max_images=max_images, timeout=timeout,
    )
    vision_desc = ""
    if use_local_vision and vision_service is not None:
        descriptions = await vision_service.describe_many(image_srcs)
        vision_desc = "；".join(d for d in descriptions if d)
    # 图片全下载失败且无描述：清空陈旧 vision_desc，防跨轮污染 RAG 索引
    if not any(data_urls) and not vision_desc:
        return {"vision_desc": ""}
    content = build_multimodal_content(msg.content, [u for u in data_urls if u])
    return {
        "messages": [HumanMessage(content=content, id=msg.id)],
        "vision_desc": vision_desc,
    }
