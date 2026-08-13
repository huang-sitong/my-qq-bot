"""技能领域数据对象。"""

from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    description: str
    body: str
