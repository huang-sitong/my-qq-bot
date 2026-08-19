"""search_documents 工具测试。"""

import asyncio

from bot.package.tools.builtin.search_documents import search_documents


class FakeDocumentStore:
    def __init__(self, hits=None):
        self.hits = hits or []

    async def search_dense(self, query, expr, thread_id, k, output_fields=None):
        return list(self.hits)

    async def search_sparse(self, query, expr, thread_id, k, output_fields=None):
        return []


def _hit(content, file_name="a.pdf", page=3):
    return {
        "id": 1,
        "file_name": file_name,
        "page": page,
        "content": content,
    }


def test_search_documents_returns_formatted_results():
    store = FakeDocumentStore(hits=[_hit("这是文档内容")])
    result = asyncio.run(search_documents("文档内容", store))
    assert "a.pdf" in result
    assert "第3页" in result
    assert "这是文档内容" in result


def test_search_documents_empty_returns_message():
    store = FakeDocumentStore()
    result = asyncio.run(search_documents("不存在", store))
    assert result == "没有找到相关的文档内容。"


def test_search_documents_requires_query():
    store = FakeDocumentStore()
    result = asyncio.run(search_documents("   ", store))
    assert "请提供" in result
