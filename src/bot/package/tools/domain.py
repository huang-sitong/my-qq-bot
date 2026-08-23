"""工具域对象 — BashConfig 归位（原 domain.bash）。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BashConfig:
    enabled: bool = True
    shell: str = "bash"
    timeout: int = 30
    max_output: int = 4000
    allowed_roots: list[str] = field(default_factory=list)
    project_root: Path = Path(".")


@dataclass(frozen=True)
class ToolSelection:
    """工具选择列表（全局启动期配置）。

    ``allowlist`` 为空表示全部可用；``denylist`` 总是最后排除。
    工具名按 LangChain ``tool.name`` 精确匹配（MCP 使用前缀后的最终名称）。
    """

    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()

    @classmethod
    def from_lists(
        cls,
        allowlist: Iterable[str] | None = None,
        denylist: Iterable[str] | None = None,
    ) -> ToolSelection:
        return cls(
            allowlist=tuple(_normalize_names(allowlist)),
            denylist=tuple(_normalize_names(denylist)),
        )

    def is_selected(self, name: str) -> bool:
        normalized = str(name).strip()
        if self.allowlist and normalized not in self.allowlist:
            return False
        return normalized not in self.denylist


def _normalize_names(names: Iterable[str] | None) -> list[str]:
    """去空白、去空项并保序去重；工具名保持原始大小写。"""
    result: list[str] = []
    for name in names or ():
        text = str(name).strip()
        if text and text not in result:
            result.append(text)
    return result


__all__ = ["BashConfig", "ToolSelection"]
