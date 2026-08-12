"""auto_reply 随机/冷却纯函数；random_value 由调用方注入，保持可测试。"""

from bot.core.utils.routing import is_explicit_request


def should_allow_auto_reply(
    channel_type: int,
    mentions: dict[str, str],
    bot_id: str,
    bot_name: str,
    auto_reply_enabled: bool,
    cooldown_elapsed: bool,
    random_value: float,
    rate: float,
) -> bool:
    if not auto_reply_enabled:
        return False
    if is_explicit_request(channel_type, bot_id, bot_name, mentions):
        return False
    if not cooldown_elapsed:
        return False
    return random_value < rate
