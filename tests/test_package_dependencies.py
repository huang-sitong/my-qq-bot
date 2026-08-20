"""门禁脚本子包粒度单测 — Task 1 失败测试"""

from pathlib import Path

from scripts.check_package_dependencies import check_runtime_dependencies


def test_new_allowlist_detects_orchestration_to_tools_violation(tmp_path: Path):
    # orchestration -> pipeline 應被禁（而 orchestration -> tools 現已允許，故測 pipeline）
    pkg = tmp_path / "bot" / "package" / "orchestration"
    pkg.mkdir(parents=True)
    (pkg / "bad.py").write_text("from bot.package.pipeline import MessagePipeline\n", encoding="utf-8")
    (tmp_path / "bot" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "bot" / "package" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "bot" / "package" / "tools" / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "bot" / "package" / "tools" / "__init__.py").write_text("", encoding="utf-8")
    violations = check_runtime_dependencies(tmp_path)
    assert any("orchestration -> pipeline" in v for v in violations), f"expected orchestration->pipeline violation, got {violations}"


def test_config_must_not_depend_on_domain():
    # Task2 後 config 不再依賴 domain，應無違規
    violations = check_runtime_dependencies(Path("src"))
    assert not any("config -> domain" in v for v in violations), f"config->domain should be fixed, got {violations}"

def test_single_source_of_ports():
    import pathlib
    contracts = pathlib.Path("src/bot/package/pipeline/contracts.py").read_text(encoding="utf-8")
    # 期望 contracts 不再定義 Protocol，僅 re-export
    assert "class MessageRouter" not in contracts
    assert "from bot.package.domain.ports import" in contracts

def test_no_duplicate_port_definitions():
    import pathlib
    domain_src = pathlib.Path("src/bot/package/domain/ports.py").read_text(encoding="utf-8")
    assert "class MessageRouter" in domain_src
    assert "class MessageSink" in domain_src

