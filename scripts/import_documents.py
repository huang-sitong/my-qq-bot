"""离线导入文档到知识库。

用法：
    uv run python scripts/import_documents.py path/to/a.pdf path/to/b.docx
    uv run python scripts/import_documents.py docs/*.pdf docs/*.xlsx
    uv run python scripts/import_documents.py --dry-run docs/*.pdf
    uv run python scripts/import_documents.py --collection documents ./docs

说明：
- 支持文件、目录、shell 通配符；目录会递归扫描受支持的扩展名；
- 仅支持 .docx / .pdf / .xlsx / .txt / .json；
- PDF 优先使用 MinerU（需配置 BOT_DOC_MINERU_ENDPOINT 或安装 mineru SDK），
  失败自动降级 LangChain / pypdf；
- 写入独立 documents collection，不参与聊天记录淘汰；
- 已导入过的文件会按内容哈希自动跳过。
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import logging
import sys
from pathlib import Path

from knowledge.document_ingestion import SUPPORTED_EXTENSIONS


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入文档到知识库")
    parser.add_argument("paths", nargs="+", help="要导入的文件/目录/通配符")
    parser.add_argument(
        "--collection",
        default="",
        help="目标 collection 名，默认使用 BOT_DOC_COLLECTION",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析和切分，不写入 Milvus，用于验证文件可被处理",
    )
    return parser.parse_args(argv)


def _expand_paths(raw_paths: list[str]) -> list[Path]:
    """把文件/目录/通配符展开为具体文件列表。"""
    files: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            files.append(path)

    for raw in raw_paths:
        matches = glob.glob(raw, recursive=True)
        if matches:
            for match in matches:
                p = Path(match)
                if p.is_dir():
                    for child in sorted(p.rglob("*")):
                        if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                            add(child)
                elif p.is_file():
                    add(p)
                else:
                    # 不存在的路径交给下游报错
                    add(p)
        else:
            p = Path(raw)
            if p.is_dir():
                for child in sorted(p.rglob("*")):
                    if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                        add(child)
            else:
                add(p)
    return files


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from common import BotConfig
    from knowledge.document_ingestion import ingest_files
    from knowledge.document_store import DocumentStore

    config = BotConfig()
    logger = logging.getLogger("import_documents")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )

    paths = _expand_paths(args.paths)
    if not paths:
        logger.error("没有找到可导入的文件")
        return 1

    unsupported = [str(p) for p in paths if p.suffix.lower() not in SUPPORTED_EXTENSIONS]
    if unsupported:
        logger.error("不支持的文件类型: %s", ", ".join(unsupported))
        return 1

    collection = args.collection or config.document_collection

    if args.dry_run:
        # dry-run 只验证解析+切分，不创建 collection / 不调用嵌入 API。
        from knowledge.document_ingestion import _LOADERS, _split_documents

        failed = False
        for path in paths:
            if not path.is_file():
                logger.error("文件不存在: %s", path)
                failed = True
                continue
            try:
                ext = path.suffix.lower()
                loader = _LOADERS[ext]
                if ext == ".pdf":
                    docs = await asyncio.to_thread(loader, path, config)
                else:
                    docs = await asyncio.to_thread(loader, path)
                chunks = _split_documents(
                    docs,
                    chunk_size=config.document_chunk_size,
                    chunk_overlap=config.document_chunk_overlap,
                )
                print(f"{path}: {len(docs)} document(s) -> {len(chunks)} chunk(s)")
            except Exception:
                logger.exception("dry-run failed for %s", path)
                failed = True
        return 1 if failed else 0

    store: DocumentStore | None = None
    try:
        store = DocumentStore(config, collection=collection)
        results = await ingest_files(config, paths, store=store)
        for item in results:
            status = item["status"]
            if status == "imported":
                print(f"[OK] {item['path']}: {item['chunks']} chunks ({item['doc_id']})")
            elif status == "duplicate":
                print(f"[SKIP] {item['path']}: already imported")
            elif status == "unsupported":
                print(f"[SKIP] {item['path']}: unsupported type")
            elif status == "missing":
                print(f"[ERROR] {item['path']}: file not found")
            else:
                print(f"[WARN] {item['path']}: {status}")
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
