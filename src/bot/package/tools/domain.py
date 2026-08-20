"""工具域对象 — BashConfig 归位（原 domain.bash）。"""

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
