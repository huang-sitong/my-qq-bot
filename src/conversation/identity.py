"""Bot 身份领域数据对象。"""

from dataclasses import dataclass


@dataclass
class BotIdentity:
    id: str = ""
    name: str = ""
