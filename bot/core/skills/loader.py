# bot/core/skills/loader.py
"""SkillRegistry — 扫描 skills/<name>/SKILL.md，解析 frontmatter 构建技能索引。

frontmatter 最小解析（不引 pyyaml）：``---`` 包住的 ``key: value`` 行。
只读 name/description 两个字段；缺失/非法一律跳过 + warning，绝不崩 bot。
技能自带的题库/脚本由 skill 正文自述路径，LLM 经 run_bash 执行（如 soup 的
import_puzzles.py / draw_puzzle.py）——loader 不感知技能数据文件。
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[a-z0-9_-]+")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str


def _parse_skill_md(path: Path) -> tuple[str, str, str] | None:
    """解析 SKILL.md → (name, description, body)；非法返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # 非 UTF-8（GBK/ANSI）解码失败也跳过，绝不崩 bot
        logger.warning("skill file unreadable: %s", path)
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    name = meta.get("name", "").strip()
    description = meta.get("description", "").strip()
    if not name or not description:
        return None
    return name, description, text[m.end():].strip()


class SkillRegistry:
    """技能注册表：内存 {name: Skill} + 目录扫描加载。"""

    def __init__(self, skills: dict[str, Skill] | None = None, index_max: int = 50) -> None:
        self._skills: dict[str, Skill] = skills or {}
        self.index_max = index_max

    @classmethod
    def from_directory(cls, skills_dir: str, index_max: int = 50) -> "SkillRegistry":
        """扫描 ``skills/<name>/SKILL.md`` 构建注册表；目录不存在 → 空注册表。"""
        skills: dict[str, Skill] = {}
        base = Path(skills_dir)
        if not base.is_dir():
            return cls(skills, index_max)
        try:
            entries = sorted(base.iterdir())
        except OSError:  # 目录不可读（权限等）→ 空注册表，绝不崩 bot
            logger.warning("skill dir unreadable: %s", base)
            return cls(skills, index_max)
        for skill_dir in entries:
            if not skill_dir.is_dir():
                continue
            md = skill_dir / "SKILL.md"
            if not md.is_file():
                continue
            parsed = _parse_skill_md(md)
            if parsed is None:
                logger.warning("skill %s: SKILL.md 缺少合法 frontmatter，跳过", skill_dir.name)
                continue
            name, description, body = parsed
            if not _NAME_RE.fullmatch(name):
                logger.warning("skill %s: name %r 非法（须 [a-z0-9_-]），跳过", skill_dir.name, name)
                continue
            skills[name] = Skill(name=name, description=description, body=body)  # 重复取最后一个

        return cls(skills, index_max)

    @property
    def total(self) -> int:
        return len(self._skills)

    def names(self) -> list[str]:
        return list(self._skills)

    def has(self, name: str) -> bool:
        return name in self._skills

    def get_skill(self, name: str) -> Skill | None:
        """返回指定技能对象；不存在返回 None。"""
        return self._skills.get(name)

    def get_body(self, name: str) -> str | None:
        skill = self._skills.get(name)
        return skill.body if skill is not None else None

    def index_lines(self) -> list[str]:
        """LLM 可见的索引行（每技能一行，排序稳定——dict 保持插入序）。"""
        return [f"- {s.name}: {s.description}" for s in self._skills.values()]

    def index_text(self) -> str:
        """完整索引文本，超过 index_max 截断并附「共 N 个」脚注。"""
        lines = self.index_lines()
        shown = lines[: self.index_max]
        text = "\n".join(shown)
        if len(lines) > self.index_max:
            text += f"\n…共 {len(lines)} 个技能，仅显示前 {self.index_max} 个"
        return text
