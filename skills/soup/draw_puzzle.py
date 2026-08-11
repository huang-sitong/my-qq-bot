#!/usr/bin/env python3
"""海龟汤抽题脚本：从 network_soupai.json 随机抽一条 {puzzle, answer} 打印。

在 bot 宿主经 run_bash 执行（或命令行直接跑），供技能开局取题用。
题库缺失/损坏/为空时打印明确指引（先运行 import_puzzles.py）并退出码 1，
绝不抛栈——LLM 看到提示即可自愈。CLI 运行时 stdout 强制 UTF-8，run_bash 可直接读取。
"""

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_PATH = HERE / "network_soupai.json"


def main(data_path: Path = DATA_PATH) -> int:
    """抽一条随机记录打印「题面/汤底」；题库不可用返回 1。"""
    if not data_path.is_file():
        print(f"题库不存在：{data_path}")
        print("请先运行 python skills/soup/import_puzzles.py 导入题库。")
        return 1
    try:
        bank = json.loads(data_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"题库损坏：{data_path}（{e}）")
        print("可先删除 network_soupai.json 再运行 import_puzzles.py 重建。")
        return 1
    if not isinstance(bank, list):
        print(f"题库格式非法（须为 list）：{data_path}")
        print("请先运行 python skills/soup/import_puzzles.py 导入题库。")
        return 1
    entries = [e for e in bank if isinstance(e, dict)]
    if not entries:
        print(f"题库为空或条目格式非法：{data_path}")
        print("请先运行 python skills/soup/import_puzzles.py 导入题库。")
        return 1
    entry = random.choice(entries)
    print(f"题面：{entry.get('puzzle', '')}")
    print(f"汤底：{entry.get('answer', '')}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
