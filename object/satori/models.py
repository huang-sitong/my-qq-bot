from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from .enums import ChannelType, LoginStatus

T = TypeVar("T")


class User(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    name: str | None = None
    nick: str | None = None
    avatar: str | None = None
    is_bot: bool | None = None


class Guild(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    name: str | None = None
    avatar: str | None = None


class GuildRole(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    name: str | None = None


class GuildMember(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    user: User | None = None
    nick: str | None = None
    avatar: str | None = None
    joined_at: int | None = None
    roles: list["GuildRole"] | None = None


class Channel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    type: ChannelType
    name: str | None = None
    parent_id: str | None = None


class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    content: str | None = None
    channel: Channel | None = None
    guild: Guild | None = None
    member: GuildMember | None = None
    user: User | None = None
    created_at: int | None = None
    updated_at: int | None = None


class Friend(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    user: User | None = None
    nick: str | None = None


class Login(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    sn: int
    platform: str | None = None
    user: User | None = None
    status: LoginStatus = LoginStatus.ONLINE
    adapter: str = ""
    features: list[str] | None = None
    proxy_urls: list[str] | None = None


class Emoji(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    name: str | None = None


class Argv(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str
    arguments: list
    options: dict


class Button(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str


class PageList[T](BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    data: list[T]
    next: str | None = None


class BidiList[T](BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    data: list[T]
    prev: str | None = None
    next: str | None = None
