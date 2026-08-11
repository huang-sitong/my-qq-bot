#!/usr/bin/env python3
"""海龟汤题库导入脚本：puzzles_raw.txt → network_soupai.json。

在 bot 宿主经 run_bash 执行（或命令行直接跑），解析同目录下 puzzles_raw.txt 的
「题面/汤底」记录，去重合并进 network_soupai.json（即 frontmatter ``data:``
指向的题库）。纯 stdlib、不依赖项目依赖，路径相对脚本自身定位，cwd 无关。

用法：
    python skills/soup/import_puzzles.py

行为：
- 幂等：按「题面+汤底」二元组判重，已存在的记录跳过，可反复执行。
  同一题面不同汤底视为两条独立记录（海龟汤常有同题多解），都保留。
- 缺字段/游离文本只打印 warning，跳过该记录，绝不崩。
- 想全量重建：先删除 network_soupai.json 再运行，即从 raw 全量生成。
- 源文件缺失 / 既有题库损坏：报错退出（退出码 1），不改动题库文件。
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW_PATH = HERE / "puzzles_raw.txt"
DATA_PATH = HERE / "network_soupai.json"

_PUZZLE_RE = re.compile(r"题面[:：]\s*(.*)")
_ANSWER_RE = re.compile(r"汤底[:：]\s*(.*)")


def _read_text(path: Path) -> str:
    """UTF-8 读取；失败回落 GBK（Windows 编辑器可能存为 GBK）。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gbk", errors="replace")


def parse_records(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """把 raw 文本解析成 (records, warnings)。

    一条记录 = 「题面：…」+「汤底：…」，字段可跨行续接（遇到空行收尾）；
    ``#`` 开头整行是注释，直接跳过。缺题面或汤底的记录跳过并记 warning。
    """
    records: list[dict[str, str]] = []
    warnings: list[str] = []
    pending: dict[str, list[str]] = {}

    def flush() -> None:
        if not pending:
            return
        puzzle = "\n".join(pending.get("puzzle", [])).strip()
        answer = "\n".join(pending.get("answer", [])).strip()
        if puzzle and answer:
            records.append({"puzzle": puzzle, "answer": answer})
        else:
            warnings.append(f"跳过缺字段记录（题面空={not puzzle}，汤底空={not answer}）")
        pending.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            flush()
            continue
        m = _PUZZLE_RE.match(line)
        if m:
            flush()  # 新记录开始，先收尾上一条（允许记录间无空行）
            pending["puzzle"] = [m.group(1).strip()]
            continue
        m = _ANSWER_RE.match(line)
        if m:
            pending["answer"] = [m.group(1).strip()]
            continue
        # 续行：并入当前未闭合字段（题面→汤底→游离文本）
        if "answer" in pending:
            pending["answer"].append(line)
        elif "puzzle" in pending:
            pending["puzzle"].append(line)
        else:
            warnings.append(f"游离文本行跳过：{line[:40]}")
    flush()
    return records, warnings


def _norm(text: str) -> str:
    """归一化：先 NFKC 折叠全半角（`！`→`!`、全角数字→半角等），再折叠空白。

    同一内容仅标点宽度/排版不同的记录视为同一（网络抓取文本常见 `!`/`！` 混用）。
    """
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _entry_key(entry: dict[str, str]) -> tuple[str, str]:
    """去重键：「题面+汤底」二元组——同题面不同汤底是两个独立记录（多解变体）。"""
    return (_norm(entry.get("puzzle", "")), _norm(entry.get("answer", "")))


def merge_records(
    new_records: list[dict[str, str]], existing: list[dict[str, str]]
) -> tuple[list[dict[str, str]], int, int]:
    """new_records 去重合并进 existing → (合并后列表, 新增数, 跳过数)。

    幂等：库里已有条目原样保留，只把不重复的新记录追加到末尾。
    """
    seen = {_entry_key(e): e for e in existing}
    merged = list(existing)
    added = skipped = 0
    for rec in new_records:
        key = _entry_key(rec)
        if not key[0]:
            continue
        if key in seen:
            skipped += 1
            continue
        seen[key] = rec
        merged.append(rec)
        added += 1
    return merged, added, skipped


def main(raw_path: Path = RAW_PATH, data_path: Path = DATA_PATH) -> int:
    """执行导入；0 成功，1 源文件缺失/既有题库损坏（不写盘）。"""
    if not raw_path.is_file():
        print(f"源文件不存在：{raw_path}")
        print("请按格式在 puzzles_raw.txt 添加「题面/汤底」记录后重试。")
        return 1

    records, warnings = parse_records(_read_text(raw_path))
    for w in warnings:
        print(f"警告：{w}")

    if data_path.is_file():
        try:
            existing = json.loads(_read_text(data_path))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"既有题库损坏，拒绝覆盖：{data_path}（{e}）")
            return 1
        if not isinstance(existing, list):
            print(f"既有题库格式非法（须为 list）：{data_path}")
            return 1
    else:
        existing = []

    merged, added, skipped = merge_records(records, existing)
    if added == 0:
        print(f"解析 {len(records)} 条，已存在跳过 {skipped}，无新增——题库无需改动。")
        return 0

    text = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    data_path.write_text(text, encoding="utf-8", newline="\n")
    print(f"新增 {added} 条，已存在跳过 {skipped} 条，题库总量 {len(merged)} 条")
    print(f"已写入：{data_path}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
