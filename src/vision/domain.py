"""视觉领域类型。

跨上下文共享的 ``ImageDescription`` DTO 已移至 ``domain.media``，此处保留
re-export 以兼容历史导入路径。
"""

from domain.media import ImageDescription

__all__ = ["ImageDescription"]
