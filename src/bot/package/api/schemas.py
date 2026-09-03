"""API 响应/请求模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class SettingItem(BaseModel):
    """单个设置项（GET /api/settings 返回的每一项）。"""

    key: str
    env: str
    value: Any
    type: str
    group: str
    label: str
    secret: bool
    has_value: bool


class SettingsResponse(BaseModel):
    settings: list[SettingItem]
    loaded_from: str
    count: int


class SettingsUpdate(BaseModel):
    """PUT /api/settings 请求体：字段名 -> 新值（可只传部分字段）。

    允许任意字段名，真正的字段校验在路由层针对 ``BotConfig.model_fields`` 做。
    """

    model_config = ConfigDict(extra="allow")

    def updates(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


__all__ = ["SettingItem", "SettingsResponse", "SettingsUpdate"]
