"""知识上下文领域对象。

跨流程共享的 ``IndexTurnTask`` DTO 已移至 ``domain.tasks``，此处保留 re-export
以兼容历史导入路径。
"""

from domain.tasks import IndexTurnTask

__all__ = ["IndexTurnTask"]
