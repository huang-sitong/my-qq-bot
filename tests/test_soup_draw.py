# tests/test_soup_draw.py
"""skills/soup/draw_puzzle.py 抽题脚本测试。

脚本是独立脚本（不在 bot 包内），经 importlib 按路径加载；纯 stdlib、不依赖项目。
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "soup" / "draw_puzzle.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("soup_draw", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bank_json(tmp_path, entries):
    data = tmp_path / "network_soupai.json"
    data.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return data


def test_draw_returns_random_entry(mod, tmp_path, capsys):
    data = _bank_json(tmp_path, [
        {"puzzle": "题A？", "answer": "底A。"},
        {"puzzle": "题B？", "answer": "底B。"},
    ])
    assert mod.main(data_path=data) == 0
    out = capsys.readouterr().out
    assert out.startswith("题面：")
    assert "汤底：" in out
    assert "题A？" in out or "题B？" in out


def test_draw_filters_non_dict_entries(mod, tmp_path, capsys):
    """混入非 dict 条目时只从 dict 条目中抽，不崩。"""
    data = _bank_json(tmp_path, [{"puzzle": "题", "answer": "底"}, "str", 3])
    assert mod.main(data_path=data) == 0
    out = capsys.readouterr().out
    assert "题面：题" in out
    assert "汤底：底" in out


def test_draw_missing_bank_hints_import(mod, tmp_path, capsys):
    rc = mod.main(data_path=tmp_path / "nope.json")
    assert rc == 1
    out = capsys.readouterr().out
    assert "题库不存在" in out
    assert "import_puzzles.py" in out


def test_draw_corrupt_bank_returns_1(mod, tmp_path, capsys):
    data = tmp_path / "network_soupai.json"
    data.write_text("{broken", encoding="utf-8")
    assert mod.main(data_path=data) == 1
    assert "损坏" in capsys.readouterr().out


def test_draw_empty_or_nonlist_returns_1(mod, tmp_path, capsys):
    for payload in ("[]", '{"a": 1}'):
        data = tmp_path / f"{len(payload)}.json"
        data.write_text(payload, encoding="utf-8")
        assert mod.main(data_path=data) == 1
    out = capsys.readouterr().out
    assert "题库为空" in out or "格式非法" in out
