# tests/test_soup_import.py
"""skills/soup/import_puzzles.py 题库导入脚本测试。

脚本是独立脚本（不在 bot 包内），经 importlib 按路径加载；纯 stdlib、不依赖项目。
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "soup" / "import_puzzles.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("soup_import", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- parse_records：raw 文本 → [{puzzle, answer}] ----

def test_parse_records_basic(mod):
    text = (
        "题面：问题A？\n"
        "汤底：答案A。\n"
        "\n"
        "题面：问题B？\n"
        "汤底：答案B。\n"
    )
    records, warnings = mod.parse_records(text)
    assert records == [
        {"puzzle": "问题A？", "answer": "答案A。"},
        {"puzzle": "问题B？", "answer": "答案B。"},
    ]
    assert warnings == []


def test_parse_records_skips_comments_and_blank(mod):
    text = (
        "# 这是注释\n"
        "\n"
        "题面：问题A？\n"
        "汤底：答案A。\n"
    )
    records, warnings = mod.parse_records(text)
    assert records == [{"puzzle": "问题A？", "answer": "答案A。"}]
    assert warnings == []


def test_parse_records_multiline_continuation(mod):
    """题面/汤底可跨行：换行内容并入字段，空行收尾。"""
    text = (
        "题面：第一行\n"
        "续行第二行\n"
        "汤底：答案第一行\n"
        "答案第二行\n"
        "\n"
    )
    records, _ = mod.parse_records(text)
    assert records == [
        {"puzzle": "第一行\n续行第二行", "answer": "答案第一行\n答案第二行"},
    ]


def test_parse_records_incomplete_record_warns(mod):
    """缺汤底/缺题面的记录跳过并记 warning，不产出记录。"""
    text = (
        "题面：只有题面\n"
        "\n"
        "汤底：只有汤底\n"
        "\n"
        "游离文本行\n"
    )
    records, warnings = mod.parse_records(text)
    assert records == []
    assert len(warnings) == 3
    assert "游离文本行" in warnings[2]


# ---- merge_records：去重合并 ----

def test_merge_records_dedup_by_puzzle_and_answer(mod):
    """按「题面+汤底」判重：同题同底跳过；同题异底是两个独立记录（多解变体）。"""
    existing = [{"puzzle": "重复题", "answer": "老底"}, {"puzzle": "旧题", "answer": "旧底"}]
    new = [
        {"puzzle": "重复题", "answer": "老底"},       # 同题同底 → 跳过
        {"puzzle": "重复题", "answer": "变体新底"},   # 同题异底 → 保留
        {"puzzle": "新题", "answer": "新底"},
    ]
    merged, added, skipped = mod.merge_records(new, existing)
    assert added == 2
    assert skipped == 1
    assert merged == existing + [
        {"puzzle": "重复题", "answer": "变体新底"},
        {"puzzle": "新题", "answer": "新底"},
    ]


# ---- main：端到端 ----

def test_main_imports_and_is_idempotent(mod, tmp_path):
    raw = tmp_path / "puzzles_raw.txt"
    data = tmp_path / "network_soupai.json"
    raw.write_text(
        "题面：新题？\n汤底：新底。\n\n题面：旧题？\n汤底：旧底。\n",
        encoding="utf-8",
    )
    data.write_text(json.dumps([{"puzzle": "旧题？", "answer": "旧底。"}], ensure_ascii=False), encoding="utf-8")

    assert mod.main(raw_path=raw, data_path=data) == 0
    bank = json.loads(data.read_text(encoding="utf-8"))
    assert len(bank) == 2
    assert {"puzzle": "新题？", "answer": "新底。"} in bank

    # 幂等：再跑一次不重复追加
    assert mod.main(raw_path=raw, data_path=data) == 0
    assert len(json.loads(data.read_text(encoding="utf-8"))) == 2


def test_main_missing_raw_returns_1(mod, tmp_path):
    data = tmp_path / "network_soupai.json"
    assert mod.main(raw_path=tmp_path / "nope.txt", data_path=data) == 1
    assert not data.exists()


def test_main_corrupt_existing_does_not_overwrite(mod, tmp_path):
    raw = tmp_path / "puzzles_raw.txt"
    data = tmp_path / "network_soupai.json"
    raw.write_text("题面：新题？\n汤底：新底。\n", encoding="utf-8")
    data.write_text("{broken json", encoding="utf-8")

    assert mod.main(raw_path=raw, data_path=data) == 1
    assert data.read_text(encoding="utf-8") == "{broken json"
