from dataclasses import dataclass

from pydantic import BaseModel

from .enums import Direction, Order
from .models import Channel, GuildRole

#
# Endpoint 定义
#

@dataclass(frozen=True)
class Endpoint:
    """Satori API 端点。"""
    resource: str
    method: str

    @property
    def path(self) -> str:
        return f"/v1/{self.resource}.{self.method}"


# User
USER_GET = Endpoint("user", "get")
USER_CHANNEL_CREATE = Endpoint("user", "channel.create")

# Channel
CHANNEL_GET = Endpoint("channel", "get")
CHANNEL_LIST = Endpoint("channel", "list")
CHANNEL_CREATE = Endpoint("channel", "create")
CHANNEL_UPDATE = Endpoint("channel", "update")
CHANNEL_DELETE = Endpoint("channel", "delete")
CHANNEL_MUTE = Endpoint("channel", "mute")

# Message
MESSAGE_CREATE = Endpoint("message", "create")
MESSAGE_GET = Endpoint("message", "get")
MESSAGE_DELETE = Endpoint("message", "delete")
MESSAGE_UPDATE = Endpoint("message", "update")
MESSAGE_LIST = Endpoint("message", "list")

# Guild
GUILD_GET = Endpoint("guild", "get")
GUILD_LIST = Endpoint("guild", "list")
GUILD_APPROVE = Endpoint("guild", "approve")

# Guild Member
GUILD_MEMBER_GET = Endpoint("guild", "member.get")
GUILD_MEMBER_LIST = Endpoint("guild", "member.list")
GUILD_MEMBER_KICK = Endpoint("guild", "member.kick")
GUILD_MEMBER_MUTE = Endpoint("guild", "member.mute")
GUILD_MEMBER_APPROVE = Endpoint("guild", "member.approve")
GUILD_MEMBER_ROLE_SET = Endpoint("guild", "member.role.set")
GUILD_MEMBER_ROLE_UNSET = Endpoint("guild", "member.role.unset")

# Guild Role
GUILD_ROLE_LIST = Endpoint("guild", "role.list")
GUILD_ROLE_CREATE = Endpoint("guild", "role.create")
GUILD_ROLE_UPDATE = Endpoint("guild", "role.update")
GUILD_ROLE_DELETE = Endpoint("guild", "role.delete")

# Friend
FRIEND_LIST = Endpoint("friend", "list")
FRIEND_DELETE = Endpoint("friend", "delete")
FRIEND_APPROVE = Endpoint("friend", "approve")

# Reaction
REACTION_CREATE = Endpoint("reaction", "create")
REACTION_DELETE = Endpoint("reaction", "delete")
REACTION_CLEAR = Endpoint("reaction", "clear")
REACTION_LIST = Endpoint("reaction", "list")

# Login
LOGIN_GET = Endpoint("login", "get")


#
# 请求参数模型
#

class UserGetParams(BaseModel):
    user_id: str


class UserChannelCreateParams(BaseModel):
    user_id: str
    guild_id: str | None = None


class ChannelGetParams(BaseModel):
    channel_id: str


class ChannelListParams(BaseModel):
    guild_id: str
    next: str | None = None


class ChannelCreateParams(BaseModel):
    guild_id: str
    data: Channel


class ChannelUpdateParams(BaseModel):
    channel_id: str
    data: Channel


class ChannelDeleteParams(BaseModel):
    channel_id: str


class ChannelMuteParams(BaseModel):
    channel_id: str
    duration: int


class MessageCreateParams(BaseModel):
    channel_id: str
    content: str


class MessageGetParams(BaseModel):
    channel_id: str
    message_id: str


class MessageDeleteParams(BaseModel):
    channel_id: str
    message_id: str


class MessageUpdateParams(BaseModel):
    channel_id: str
    message_id: str
    content: str


class MessageListParams(BaseModel):
    channel_id: str
    next: str | None = None
    direction: Direction | None = None
    limit: int | None = None
    order: Order | None = None


class GuildGetParams(BaseModel):
    guild_id: str


class GuildListParams(BaseModel):
    next: str | None = None


class GuildApproveParams(BaseModel):
    message_id: str
    approve: bool
    comment: str | None = None


class GuildMemberGetParams(BaseModel):
    guild_id: str
    user_id: str


class GuildMemberListParams(BaseModel):
    guild_id: str
    next: str | None = None


class GuildMemberKickParams(BaseModel):
    guild_id: str
    user_id: str
    permanent: bool | None = None


class GuildMemberMuteParams(BaseModel):
    guild_id: str
    user_id: str
    duration: int


class GuildMemberApproveParams(BaseModel):
    message_id: str
    approve: bool
    comment: str | None = None


class GuildMemberRoleSetParams(BaseModel):
    guild_id: str
    user_id: str
    role_id: str


class GuildMemberRoleUnsetParams(BaseModel):
    guild_id: str
    user_id: str
    role_id: str


class GuildRoleListParams(BaseModel):
    guild_id: str
    next: str | None = None


class GuildRoleCreateParams(BaseModel):
    guild_id: str
    role: GuildRole


class GuildRoleUpdateParams(BaseModel):
    guild_id: str
    role_id: str
    role: GuildRole


class GuildRoleDeleteParams(BaseModel):
    guild_id: str
    role_id: str


class FriendListParams(BaseModel):
    next: str | None = None


class FriendDeleteParams(BaseModel):
    user_id: str


class FriendApproveParams(BaseModel):
    message_id: str
    approve: bool
    comment: str | None = None


class ReactionCreateParams(BaseModel):
    channel_id: str
    message_id: str
    emoji: str


class ReactionDeleteParams(BaseModel):
    channel_id: str
    message_id: str
    emoji: str
    user_id: str | None = None


class ReactionClearParams(BaseModel):
    channel_id: str
    message_id: str
    emoji: str | None = None


class ReactionListParams(BaseModel):
    channel_id: str
    message_id: str
    emoji: str
    next: str | None = None
