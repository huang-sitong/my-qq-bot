"""search_documents 工具（纯函数）。

检索独立 documents collection 中的文档知识，返回文本块供 LLM 使用。
与 search_chat_history 不同，这里不依赖 sender/receiver/time 等聊天元数据，
而是展示文件名、页码、内容片段。
"""

from __future__ import annotations

from bot.package.knowledge.document_store import DocumentStore
from bot.package.knowledge.milvus import _esc
from bot.package.knowledge.rrf import rrf_merge


def _format_results(results: list[dict]) -> str:
    if not results:
        return "没有找到相关的文档内容。"
    lines = []
    for r in results:
        file_name = r.get("file_name") or "未知文件"
        page = r.get("page") or 0
        loc = f"第{page}页" if page else ""
        source = f"{file_name} {loc}".strip()
        content = (r.get("content") or "").replace("\n", "\\n")
        lines.append(f"[{source}] {content}")
    return "\n".join(lines)


def _build_expr(filename: str, file_type: str) -> str:
    conds = []
    if filename:
        # 文件名前缀匹配，转义 Milvus 字符串
        conds.append(f"file_name like '{_esc(filename)}%'")
    if file_type:
        conds.append(f"file_type == '{_esc(file_type)}'")
    return " && ".join(conds)


async def search_documents(
    query: str,
    document_store: DocumentStore,
    filename: str = "",
    file_type: str = "",
    top_k: int = 5,
) -> str:
    """按语义/关键词检索文档知识，返回格式化文本。

    filename 仅做前缀匹配；file_type 为如 pdf/docx/xlsx/txt/json。
    """
    query = query.strip()
    if not query:
        return "请提供检索关键词或问题。"

    expr = _build_expr(filename.strip(), file_type.strip().lower())
    dense = await document_store.search_dense(query, expr, None, 50)
    sparse = await document_store.search_sparse(query, expr, None, 50)
    hits = rrf_merge([dense, sparse])[:top_k]
    return _format_results(hits)
