"""会话编排上下文。

存放 LangGraph 工作流组装与图节点：图定义、LLM 节点、确定性动作节点。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .compaction import ContextCompactor
    from .graph import create_graph

__all__ = [
    "ContextCompactor",
    "create_graph",
]


def __getattr__(name: str):
    if name == "ContextCompactor":
        from .compaction import ContextCompactor
        return ContextCompactor
    if name == "create_graph":
        from .graph import create_graph
        return create_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
