"""跨上下文共享媒体/视觉 DTO。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageDescription:
    image_src: str
    description: str
