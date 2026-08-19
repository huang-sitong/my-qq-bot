from pydantic import BaseModel, ConfigDict

from .models import (
    Argv,
    Button,
    Channel,
    Emoji,
    Guild,
    GuildMember,
    GuildRole,
    Login,
    Message,
    User,
)


class Signal(BaseModel):
    """WebSocket 信号帧，对应 Satori 协议中的 op 消息。

    op 码说明:
        0 — EVENT: body 为事件数据
        1 — PING: 服务端心跳，body 可选
        2 — PONG: 客户端心跳回复
        3 — IDENTIFY: 客户端鉴权
        4 — LOGIN: 登录状态更新，body 包含 logins 列表
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    op: int
    body: dict | None = None


class EventBody(BaseModel):
    """op=0 事件体，包含事件元数据及关联资源。"""
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    sn: int
    type: str
    self_id: str | None = None
    platform: str | None = None
    timestamp: int | None = None

    # 事件关联资源（不同事件类型携带不同字段）
    channel: Channel | None = None
    guild: Guild | None = None
    member: GuildMember | None = None
    user: User | None = None
    message: Message | None = None
    login: Login | None = None
    button: Button | None = None
    argv: Argv | None = None
    emoji: Emoji | None = None
    role: GuildRole | None = None


class LoginList(BaseModel):
    """op=4 登录状态更新体。"""
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    logins: list[Login]
    proxy_urls: list[str] | None = None
