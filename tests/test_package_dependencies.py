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
    # 兼容层已彻底移除，pipeline/contracts.py 应不存在，domain/ports 为唯一源
    assert not pathlib.Path("src/bot/package/pipeline/contracts.py").exists(), "pipeline/contracts.py should be removed"
    # 确保没有残留对旧路径的导入
    for path in pathlib.Path("src").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "from bot.package.pipeline.contracts import" not in src
        assert "import bot.package.pipeline.contracts" not in src


def test_no_duplicate_port_definitions():
    import pathlib
    domain_src = pathlib.Path("src/bot/package/domain/ports.py").read_text(encoding="utf-8")
    assert "class MessageRouter" in domain_src
    assert "class MessageSink" in domain_src
