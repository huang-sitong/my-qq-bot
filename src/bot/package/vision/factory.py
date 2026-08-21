"""视觉上下文工厂。"""

from __future__ import annotations

import logging

from bot.package.config import BotConfig

from .service import VisionService

logger = logging.getLogger(__name__)


def create_vision_service(config: BotConfig) -> VisionService | None:
    """按配置创建 VisionService；禁用或缺参时返回 None。"""
    if not config.vision_enabled:
        return None
    if not config.vision_base_url:
        logger.warning("vision_enabled but vision_base_url is empty; disabling vision")
        return None
    return VisionService(
        base_url=config.vision_base_url,
        model=config.vision_model,
        api_key=config.vision_api_key,
        timeout=config.vision_timeout,
        max_images=config.vision_max_images,
    )
