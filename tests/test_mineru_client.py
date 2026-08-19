"""MinerU v4 HTTP 客户端测试。

用 ``httpx.MockTransport`` 伪造批量签名上传/轮询/zip 下载，覆盖：
- 未配置时跳过（返回 None）；
- 仅配 API Key 回落云端默认地址；
- 完整成功流程（申请上传链接 → PUT → 轮询 done → 下载 full.md）；
- failed 状态、网络错误、超时均降级返回 None。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx

from bot.package.config import BotConfig
from bot.package.knowledge.mineru_client import (
    mineru_agent_base_url,
    mineru_base_url,
    parse_pdf,
    parse_pdf_agent,
)


def _config(**overrides) -> BotConfig:
    kwargs = {
        "_env_file": None,
        "embed_dimensions": 4,
        "document_mineru_endpoint": None,
        "document_mineru_api_key": None,
    }
    kwargs.update(overrides)
    return BotConfig(**kwargs)


def _zip_bytes(content: str, name: str = "full.md") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, content)
    return buf.getvalue()


def _write_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4 fake pdf content")
    return path


# ---------------------------------------------------------------------------
# mineru_base_url
# ---------------------------------------------------------------------------


def test_mineru_base_url_not_configured():
    assert mineru_base_url(_config()) is None


def test_mineru_base_url_explicit_endpoint_strips_trailing_slash():
    config = _config(document_mineru_endpoint="http://mineru:8000/")
    assert mineru_base_url(config) == "http://mineru:8000"


def test_mineru_base_url_cloud_fallback_with_only_api_key():
    config = _config(document_mineru_api_key="sk-abc")
    assert mineru_base_url(config) == "https://mineru.net"


def test_mineru_base_url_prefers_explicit_endpoint():
    config = _config(
        document_mineru_endpoint="http://mineru:8000",
        document_mineru_api_key="sk-abc",
    )
    assert mineru_base_url(config) == "http://mineru:8000"


# ---------------------------------------------------------------------------
# parse_pdf 完整流程
# ---------------------------------------------------------------------------


def test_parse_pdf_success(tmp_path):
    path = _write_pdf(tmp_path)
    zip_url = "https://cdn.example/full.zip"
    markdown = "# 标题\n\n这是 MinerU 解析出的正文。"
    calls = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/api/v4/file-urls/batch"):
            assert request.headers["Authorization"] == "Bearer sk-abc"
            body = json.loads(request.content)
            assert body["files"][0]["name"] == "sample.pdf"
            assert body["model_version"] == "pipeline"
            return httpx.Response(200, json={
                "code": 0,
                "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/put"]},
                "msg": "ok",
            })
        if request.method == "PUT":
            assert request.content.startswith(b"%PDF-1.4")
            return httpx.Response(200)
        if request.method == "GET" and request.url.path.endswith("/api/v4/extract-results/batch/batch-1"):
            calls["poll"] += 1
            if calls["poll"] == 1:
                return httpx.Response(200, json={
                    "code": 0,
                    "data": {"extract_result": [{"file_name": "sample.pdf", "state": "running"}]},
                })
            return httpx.Response(200, json={
                "code": 0,
                "data": {"extract_result": [{"file_name": "sample.pdf", "state": "done", "full_zip_url": zip_url}]},
            })
        if request.method == "GET" and str(request.url) == zip_url:
            return httpx.Response(200, content=_zip_bytes(markdown))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = _config(document_mineru_api_key="sk-abc")
    result = parse_pdf(path, config, transport=httpx.MockTransport(handler))
    assert result == markdown
    assert calls["poll"] >= 2


def test_parse_pdf_not_configured_returns_none(tmp_path):
    path = _write_pdf(tmp_path)
    assert parse_pdf(path, _config()) is None


def test_parse_pdf_failed_state_returns_none(tmp_path):
    path = _write_pdf(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={
                "code": 0,
                "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/put"]},
            })
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, json={
                "code": 0,
                "data": {"extract_result": [{"file_name": "sample.pdf", "state": "failed", "err_msg": "file page count exceeds limit"}]},
            })
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = _config(document_mineru_api_key="sk-abc")
    assert parse_pdf(path, config, transport=httpx.MockTransport(handler)) is None


def test_parse_pdf_apply_url_error_returns_none(tmp_path):
    path = _write_pdf(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": "A0202", "data": None, "msg": "Token 错误"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = _config(document_mineru_api_key="sk-bad")
    assert parse_pdf(path, config, transport=httpx.MockTransport(handler)) is None


def test_parse_pdf_network_error_returns_none(tmp_path):
    path = _write_pdf(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    config = _config(document_mineru_api_key="sk-abc")
    assert parse_pdf(path, config, transport=httpx.MockTransport(handler)) is None


def test_parse_pdf_poll_timeout_returns_none(tmp_path):
    path = _write_pdf(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={
                "code": 0,
                "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/put"]},
            })
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, json={
                "code": 0,
                "data": {"extract_result": [{"file_name": "sample.pdf", "state": "running"}]},
            })
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = _config(document_mineru_api_key="sk-abc", document_mineru_timeout=1)
    # 轮询间隔 3s + 超时 1s：第一次 running 后会在第二次轮询前命中 deadline → 降级
    assert parse_pdf(path, config, transport=httpx.MockTransport(handler)) is None


# ---------------------------------------------------------------------------
# parse_pdf_agent —— Agent 轻量解析
# ---------------------------------------------------------------------------


def test_mineru_agent_base_url_cloud_fallback():
    assert mineru_agent_base_url(_config()) == "https://mineru.net"


def test_mineru_agent_base_url_prefers_explicit_endpoint():
    config = _config(document_mineru_endpoint="http://mineru:8000")
    assert mineru_agent_base_url(config) == "http://mineru:8000"


def test_parse_pdf_agent_success(tmp_path):
    path = _write_pdf(tmp_path)
    markdown_url = "https://cdn.example/full.md"
    markdown = "# Agent 解析结果\n\n免 Token 轻量解析。"
    calls = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/api/v1/agent/parse/file"):
            # 免 Token：不应带 Authorization 头
            assert "Authorization" not in request.headers
            body = json.loads(request.content)
            assert body["file_name"] == "sample.pdf"
            assert body["enable_formula"] is True
            return httpx.Response(200, json={
                "code": 0,
                "data": {"task_id": "agent-task-1", "file_url": "https://upload.example/put"},
            })
        if request.method == "PUT":
            assert request.content.startswith(b"%PDF-1.4")
            return httpx.Response(200)
        if request.method == "GET" and str(request.url).endswith("/api/v1/agent/parse/agent-task-1"):
            calls["poll"] += 1
            if calls["poll"] == 1:
                return httpx.Response(200, json={"code": 0, "data": {"state": "running"}})
            return httpx.Response(200, json={"code": 0, "data": {"state": "done", "markdown_url": markdown_url}})
        if request.method == "GET" and str(request.url) == markdown_url:
            return httpx.Response(200, text=markdown)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = _config()  # 无需 API Key / endpoint
    assert parse_pdf_agent(path, config, transport=httpx.MockTransport(handler)) == markdown
    assert calls["poll"] >= 2


def test_parse_pdf_agent_disabled_returns_none(tmp_path):
    path = _write_pdf(tmp_path)
    config = _config(document_mineru_agent_enabled=False)
    assert parse_pdf_agent(path, config) is None


def test_parse_pdf_agent_too_large_returns_none(tmp_path):
    path = tmp_path / "big.pdf"
    path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("file too large should not hit the network")

    config = _config()
    assert parse_pdf_agent(path, config, transport=httpx.MockTransport(handler)) is None


def test_parse_pdf_agent_failed_state_returns_none(tmp_path):
    path = _write_pdf(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={
                "code": 0,
                "data": {"task_id": "agent-task-1", "file_url": "https://upload.example/put"},
            })
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"state": "failed", "err_msg": "page limit exceeded"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = _config()
    assert parse_pdf_agent(path, config, transport=httpx.MockTransport(handler)) is None


def test_parse_pdf_agent_network_error_returns_none(tmp_path):
    path = _write_pdf(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    config = _config()
    assert parse_pdf_agent(path, config, transport=httpx.MockTransport(handler)) is None

