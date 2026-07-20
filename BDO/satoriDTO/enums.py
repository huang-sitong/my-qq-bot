from enum import IntEnum, StrEnum


class ChannelType(IntEnum):
    TEXT = 0
    DIRECT = 1
    CATEGORY = 2
    VOICE = 3


class LoginStatus(IntEnum):
    OFFLINE = 0
    ONLINE = 1
    CONNECT = 2
    DISCONNECT = 3
    RECONNECT = 4


class Direction(StrEnum):
    before = "before"
    after = "after"
    around = "around"


class Order(StrEnum):
    asc = "asc"
    desc = "desc"
