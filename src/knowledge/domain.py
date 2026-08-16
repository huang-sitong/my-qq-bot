from dataclasses import dataclass


@dataclass(frozen=True)
class IndexTurnTask:
    thread_id: str
    user_id: str
    user_name: str
    bot_id: str
    bot_name: str
    user_message: str
    bot_reply: str
