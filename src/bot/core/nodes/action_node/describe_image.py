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
from domain.bot.vision import ImageDescription


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


def _message_image_srcs(message: HumanMessage) -> list[str]:
    """从 HumanMessage 元数据取该消息自己的图片 URL。"""
    return list(message.additional_kwargs.get("image_srcs") or [])


def _message_auto_reply(message: HumanMessage, default: bool = False) -> bool:
    """从 HumanMessage 元数据取该消息是否由自动回复策略触发。"""
    return bool(message.additional_kwargs.get("auto_reply", default))


def _copy_message_with_content(message: HumanMessage, content) -> HumanMessage:
    """保留 id/name/additional_kwargs，只替换 content（供 reducer 原位更新）。"""
    return HumanMessage(
        content=content,
        id=message.id,
        name=message.name,
        additional_kwargs=message.additional_kwargs or {},
    )


def _vision_result(
    updates: list[HumanMessage],
    descriptions: list[ImageDescription],
    *,
    has_content: bool,
) -> dict:
    """组装节点返回；全失败时返回空列表清掉陈旧 vision_desc。"""
    if not has_content:
        return {"vision_desc": []}
    result: dict = {}
    if updates:
        result["messages"] = updates
    result["vision_desc"] = descriptions
    return result


async def describe_image_node(
    state: BotState,
    vision_service: VisionService | None = None,
    llm_multimodal: bool = False,
    max_images: int = 3,
    timeout: float = 60.0,
) -> dict:
    """批量图片处理：逐条 HumanMessage 处理自己的图片，返回逐图描述。

    失败时降级为 [图片] 占位符（多模态全下载失败 → 文本只留占位符）。
    ``vision_desc`` 为 ``list[ImageDescription]``，每个元素携带 ``image_src``，
    明确该描述对应哪一张图片。
    """
    messages = state.get("messages") or []
    targets = [
        message
        for message in messages
        if isinstance(message, HumanMessage) and _message_image_srcs(message)
    ]
    if not targets:
        return {}
    if llm_multimodal:
        return await _describe_all_multimodal(
            targets, vision_service, max_images, timeout,
            auto_reply_default=state.get("auto_reply", False),
        )

    # 纯文本模式（现状）：本地视觉描述 → [图片：描述] 原位替换；
    # auto_reply 图片轮保留占位符并清空陈旧 vision_desc。
    local_targets = [
        message
        for message in targets
        if not _message_auto_reply(message, state.get("auto_reply", False))
    ]
    if not local_targets:
        return {"vision_desc": []}
    return await _describe_all_local(local_targets, vision_service)


async def _describe_all_local(
    messages: list[HumanMessage],
    vision_service: VisionService | None,
) -> dict:
    """本地视觉模式：逐条消息描述，所有成功图片返回结构化描述。"""
    if vision_service is None:
        return {}
    updates: list[HumanMessage] = []
    descriptions: list[ImageDescription] = []
    has_content = False
    for message in messages:
        image_srcs = _message_image_srcs(message)
        descs = await vision_service.describe_many(image_srcs)
        descriptions.extend(
            ImageDescription(image_src=src, description=desc)
            for src, desc in zip(image_srcs, descs)
        )
        new_content = replace_placeholders(message.content, descs)
        if new_content != message.content:
            updates.append(_copy_message_with_content(message, new_content))
        if any(desc for desc in descs):
            has_content = True
    return _vision_result(updates, descriptions, has_content=has_content)


async def _describe_all_multimodal(
    messages: list[HumanMessage],
    vision_service: VisionService | None,
    max_images: int,
    timeout: float,
    auto_reply_default: bool = False,
) -> dict:
    """多模态模式：逐条下载图片 → 原位替换为 content 数组；本地视觉仅产 vision_desc。"""
    updates: list[HumanMessage] = []
    descriptions: list[ImageDescription] = []
    has_content = False
    for message in messages:
        image_srcs = _message_image_srcs(message)
        data_urls = await download_images_as_data_urls(
            image_srcs, max_images=max_images, timeout=timeout,
        )
        visible_urls = [url for url in data_urls if url]
        if (
            not _message_auto_reply(message, auto_reply_default)
            and vision_service is not None
        ):
            descs = await vision_service.describe_many(image_srcs)
            descriptions.extend(
                ImageDescription(image_src=src, description=desc)
                for src, desc in zip(image_srcs, descs)
            )
            if any(desc for desc in descs):
                has_content = True
        if visible_urls:
            content = build_multimodal_content(message.content, visible_urls)
            updates.append(_copy_message_with_content(message, content))
            has_content = True
    return _vision_result(updates, descriptions, has_content=has_content)
