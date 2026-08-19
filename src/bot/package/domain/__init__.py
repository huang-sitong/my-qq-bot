"""共享领域模型导出。

- 跨上下文共享 DTO 位于 ``domain.tasks`` / ``domain.media`` / ``domain.bash``
- 端口抽象位于 ``domain.ports``
- Satori 协议模型位于 ``bot.package.platform.satori``

业务领域模型按限界上下文拆分到 ``bot.package.commands`` /
``bot.package.conversation`` / ``bot.package.skill`` / ``bot.package.knowledge`` /
``bot.package.memory`` / ``bot.package.vision``。
"""

__all__ = [
    "BashConfig",
    "ImageDescription",
    "IndexTurnTask",
]

_module_map: dict[str, str] = {
    "BashConfig": "bash",
    "ImageDescription": "media",
    "IndexTurnTask": "tasks",
}


def __getattr__(name: str):
    module_name = _module_map.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f".{module_name}", __package__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
