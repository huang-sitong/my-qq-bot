"""视觉结果领域类型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageDescription:
    image_src: str
    description: str
