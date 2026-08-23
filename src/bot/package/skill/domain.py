"""技能领域数据对象。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    description: str
    body: str


@dataclass(frozen=True)
class SkillSelection:
    """技能选择列表（全局启动期配置）。

    ``allowlist`` 为空表示全部可用；``denylist`` 总是最后排除。
    名称统一按小写匹配。
    """

    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()

    @classmethod
    def from_lists(
        cls,
        allowlist: Iterable[str] | None = None,
        denylist: Iterable[str] | None = None,
    ) -> SkillSelection:
        return cls(
            allowlist=tuple(_normalize_names(allowlist)),
            denylist=tuple(_normalize_names(denylist)),
        )

    def is_selected(self, name: str) -> bool:
        normalized = name.strip().lower()
        if self.allowlist and normalized not in self.allowlist:
            return False
        return normalized not in self.denylist


def _normalize_names(names: Iterable[str] | None) -> list[str]:
    """去空白、去空项、小写并保序去重。"""
    result: list[str] = []
    for name in names or ():
        text = str(name).strip().lower()
        if text and text not in result:
            result.append(text)
    return result


__all__ = ["Skill", "SkillSelection"]
