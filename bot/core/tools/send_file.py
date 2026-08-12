"""send_file 纯函数：把 bot 宿主上的本地文件交付到当前会话。

LLBot 的 Satori ``<file>`` 元素目前只 fetch 不发送（上游 TODO），因此普通文件
走 LLBot Satori server 暴露的 OneBot11 internal 上传动作；图片走标准
``message.create`` 的 ``<img>`` 消息。路径白名单与 run_bash 一致。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _within_roots(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _validate_name(name: str) -> str | None:
    if "/" in name or "\\" in name:
        return "文件名不能包含 / 或 \\"
    return None


async def send_file(
    path: str,
    name: str = "",
    channel_id: str = "",
    *,
    file_sender,
    roots: list[Path],
) -> str:
    """发送单个本地文件到当前会话；返回给 LLM 的可读结果。"""
    local = Path(path).expanduser().resolve()
    if not local.is_file():
        return f"文件不存在或不是文件：{local}"
    if not _within_roots(local, roots):
        shown = ", ".join(str(root) for root in roots)
        return f"文件路径 {local} 不在允许发送的根目录内。允许：{shown}"
    if not channel_id:
        return "缺少当前频道信息，无法发送文件。"

    final_name = name.strip() or local.name
    if error := _validate_name(final_name):
        return error

    try:
        result = await file_sender.send_file(channel_id, str(local), final_name)
    except Exception:
        logger.exception("send_file failed for %s", local)
        return "文件发送失败。"

    if isinstance(result, dict) and result.get("status") not in (None, "ok"):
        return f"文件发送失败：{result}"
    return f"文件已发送到当前会话：{final_name}"
