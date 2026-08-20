"""门禁脚本子包粒度单测 — Task 1 失败测试"""

from pathlib import Path
from scripts.check_package_dependencies import check_runtime_dependencies


def test_new_allowlist_detects_orchestration_to_tools_violation(tmp_path: Path):
    pkg = tmp_path / "bot" / "package" / "orchestration"
    pkg.mkdir(parents=True)
    (pkg / "bad.py").write_text("from bot.package.tools import build_tools\n", encoding="utf-8")
    (tmp_path / "bot" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "bot" / "package" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "bot" / "package" / "tools" / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "bot" / "package" / "tools" / "__init__.py").write_text("", encoding="utf-8")
    violations = check_runtime_dependencies(tmp_path)
    assert any("orchestration -> tools" in v for v in violations), f"expected orchestration->tools violation, got {violations}"


def test_config_must_not_depend_on_domain():
    # 現狀 config/settings.py from domain.prompts import DEFAULT_PERSONA_PROMPT 應被判違規
    violations = check_runtime_dependencies(Path("src"))
    assert any("config -> domain" in v for v in violations), f"expected config->domain violation, got {violations}"
