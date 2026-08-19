"""MinerU 精准解析 + Agent 轻量解析 —— 纯 HTTP 客户端（替代 Python SDK）。

接入 .others/mineru.md 的两套 API，供 PDF 解析做多级降级：

**精准解析 API（v4，需 Token）**
- 本地文件走批量签名上传：
  1. POST ``/api/v4/file-urls/batch`` 申请上传链接（Bearer Token）；
  2. 用 PUT 把文件二进制直接传到签名 URL；
  3. 轮询 ``/api/v4/extract-results/batch/{batch_id}``；
  4. state=done 后下载结果 zip，取出 ``full.md``（Markdown）。

**Agent 轻量解析 API（v1，免 Token，IP 限频）**
- 本地文件走签名上传：
  1. POST ``/api/v1/agent/parse/file`` 申请上传链接；
  2. 用 PUT 把文件二进制直接传到签名 URL；
  3. 轮询 ``/api/v1/agent/parse/{task_id}``；
  4. state=done 后从 ``markdown_url`` 直接取 Markdown（无 zip）。
- 限制：≤10MB、≤20 页；不支持 HTML。

- 全部使用同步 ``httpx.Client``，由调用方用 ``asyncio.to_thread`` 包装，
  与 ``document_ingestion`` 现有 loader 的调用方式保持一致。
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
import zipfile
from pathlib import Path

import httpx

from bot.package.config import BotConfig

logger = logging.getLogger(__name__)

# 云端默认基地址；兼容自建 MinerU HTTP 服务（endpoint 指向内网地址即可）
_DEFAULT_BASE_URL = "https://mineru.net"

# 使用的模型版本（pipeline 轻量 / vlm 高精度，选取全局默认 pipeline）
_MODEL_VERSION = "pipeline"

# 轮询间隔（秒）
_POLL_INTERVAL = 3

# Agent 轻量解析固定参数（默认中英文、开表格/公式、关 OCR）
_AGENT_LANGUAGE = "ch"
_AGENT_ENABLE_TABLE = True
_AGENT_IS_OCR = False
_AGENT_ENABLE_FORMULA = True

# Agent 轻量解析文件大小上限（10MB）
_AGENT_MAX_BYTES = 10 * 1024 * 1024


class MinerUError(RuntimeError):
    """MinerU API 调用失败，调用方应降级处理。"""


def mineru_base_url(config: BotConfig) -> str | None:
    """返回 MinerU API 基地址；未配置 endpoint 且未配置 API Key 时返回 None。

    - 显式配置 ``BOT_DOC_MINERU_ENDPOINT`` → 用该地址（自建服务）；
    - 只配置了 ``BOT_DOC_MINERU_API_KEY`` → 回落云端 ``https://mineru.net``；
    - 两者都未配置 → None（跳过 MinerU，PDF 走 LangChain/pypdf 降级）。
    """
    endpoint = getattr(config, "document_mineru_endpoint", None)
    if endpoint and endpoint.strip():
        return endpoint.strip().rstrip("/")
    if config.document_mineru_api_key:
        return _DEFAULT_BASE_URL
    return None


def mineru_agent_base_url(config: BotConfig) -> str:
    """Agent 轻量解析的 API 基地址（免 Token，直接回落云端）。

    - 显式配置 ``BOT_DOC_MINERU_ENDPOINT`` → 用该地址；
    - 未配置 → 云端 ``https://mineru.net``（Agent 接口免登录即可用）。
    """
    endpoint = getattr(config, "document_mineru_endpoint", None)
    if endpoint and endpoint.strip():
        return endpoint.strip().rstrip("/")
    return _DEFAULT_BASE_URL


def _auth_headers(config: BotConfig) -> dict[str, str]:
    """组装 JSON 请求头；API Key 存在时追加 Bearer Token。"""
    headers = {"Content-Type": "application/json"}
    api_key = config.document_mineru_api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _file_short_hash(path: Path) -> str:
    """文件内容哈希前 8 位，作为业务侧 data_id。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def _request_batch_urls(client: httpx.Client, base: str, path: Path, config: BotConfig) -> tuple[str, str]:
    """申请批量签名上传链接，返回 (batch_id, 首个文件的 file_url)。"""
    payload = {
        "files": [{"name": path.name, "data_id": f"doc-{_file_short_hash(path)}"}],
        "model_version": _MODEL_VERSION,
    }
    resp = client.post(f"{base}/api/v4/file-urls/batch", headers=_auth_headers(config), json=payload)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data") or {}
    batch_id = data.get("batch_id")
    urls = data.get("file_urls") or []
    if body.get("code") != 0 or not batch_id or not urls:
        raise MinerUError(f"apply MinerU upload url failed: {body.get('msg', 'unknown')}")
    return batch_id, urls[0]


def _upload_file(client: httpx.Client, file_url: str, path: Path) -> None:
    """PUT 上传文件二进制到签名 URL（上传阶段不设 Content-Type）。"""
    resp = client.put(file_url, content=path.read_bytes())
    if resp.status_code not in (200, 201):
        raise MinerUError(f"MinerU file upload failed with HTTP {resp.status_code}")


def _poll_batch_result(
    client: httpx.Client,
    base: str,
    batch_id: str,
    config: BotConfig,
    timeout: float,
) -> str:
    """轮询批量结果直到 done，返回 full_zip_url；failed/超时抛 MinerUError。"""
    endpoint = f"{base}/api/v4/extract-results/batch/{batch_id}"
    deadline = time.monotonic() + timeout
    last_state = "pending"
    while time.monotonic() < deadline:
        resp = client.get(endpoint, headers=_auth_headers(config))
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or {}
        results = data.get("extract_result") or []
        result = results[0] if results else {}
        state = result.get("state") or "pending"
        last_state = state
        if state == "done":
            full_zip_url = result.get("full_zip_url")
            if full_zip_url:
                return full_zip_url
        if state == "failed":
            raise MinerUError(result.get("err_msg") or "MinerU parse failed")
        time.sleep(_POLL_INTERVAL)
    raise MinerUError(f"MinerU poll timeout after {timeout:.0f}s (last_state={last_state})")


def _download_markdown(client: httpx.Client, zip_url: str) -> str:
    """下载结果 zip 并提取 Markdown 正文；提取失败抛 MinerUError。"""
    resp = client.get(zip_url)
    resp.raise_for_status()
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            for name in ("full.md", "main.html"):
                if name in archive.namelist():
                    return archive.read(name).decode("utf-8", errors="replace")
            md_candidates = sorted(
                n for n in archive.namelist()
                if n.lower().endswith((".md", ".markdown"))
            )
            if md_candidates:
                return archive.read(md_candidates[0]).decode("utf-8", errors="replace")
    except MinerUError:
        raise
    except Exception:
        logger.debug("MinerU zip extraction failed", exc_info=True)
    raise MinerUError("no markdown found in MinerU result zip")


def _request_agent_file_url(client: httpx.Client, base: str, path: Path) -> tuple[str, str]:
    """申请 Agent 轻量解析签名上传链接，返回 (task_id, file_url)。"""
    payload = {
        "file_name": path.name,
        "language": _AGENT_LANGUAGE,
        "enable_table": _AGENT_ENABLE_TABLE,
        "is_ocr": _AGENT_IS_OCR,
        "enable_formula": _AGENT_ENABLE_FORMULA,
    }
    resp = client.post(f"{base}/api/v1/agent/parse/file", json=payload)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data") or {}
    task_id = data.get("task_id")
    file_url = data.get("file_url")
    if body.get("code") != 0 or not task_id or not file_url:
        raise MinerUError(f"request MinerU agent upload url failed: {body.get('msg', 'unknown')}")
    return task_id, file_url


def _poll_agent_result(
    client: httpx.Client,
    base: str,
    task_id: str,
    config: BotConfig,
    timeout: float,
) -> str:
    """轮询 Agent 任务直到 done，返回 markdown_url；failed/超时抛 MinerUError。"""
    endpoint = f"{base}/api/v1/agent/parse/{task_id}"
    deadline = time.monotonic() + timeout
    last_state = "pending"
    while time.monotonic() < deadline:
        resp = client.get(endpoint)
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or {}
        state = data.get("state") or "pending"
        last_state = state
        if state == "done":
            markdown_url = data.get("markdown_url")
            if markdown_url:
                return markdown_url
        if state == "failed":
            raise MinerUError(data.get("err_msg") or "MinerU agent parse failed")
        time.sleep(_POLL_INTERVAL)
    raise MinerUError(f"MinerU agent poll timeout after {timeout:.0f}s (last_state={last_state})")


def parse_pdf_agent(
    path: str | Path,
    config: BotConfig,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """通过 MinerU Agent 轻量 API（v1）解析本地 PDF，返回 Markdown 文本。

    开关 ``BOT_DOC_MINERU_AGENT_ENABLED`` 关闭、文件超过 10MB、或任意一步
    失败/超时 → 返回 None，由调用方继续降级。
    """
    path = Path(path)
    if not getattr(config, "document_mineru_agent_enabled", True):
        return None
    try:
        if path.stat().st_size > _AGENT_MAX_BYTES:
            logger.info("skip MinerU agent for %s: exceeds 10MB lightweight limit", path.name)
            return None
    except OSError:
        return None

    base = mineru_agent_base_url(config)
    timeout = float(getattr(config, "document_mineru_timeout", 300) or 300)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10),
            transport=transport,
        ) as client:
            task_id, file_url = _request_agent_file_url(client, base, path)
            _upload_file(client, file_url, path)
            markdown_url = _poll_agent_result(client, base, task_id, config, timeout)
            md_resp = client.get(markdown_url)
            md_resp.raise_for_status()
            return md_resp.text
    except Exception:
        logger.warning("MinerU agent parse failed for %s, fallback to LangChain", path, exc_info=True)
        return None


def parse_pdf(
    path: str | Path,
    config: BotConfig,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """通过 MinerU v4 批量上传解析本地 PDF，返回 Markdown 文本。

    未配置 MinerU（既无 endpoint 也无 API Key）或任意一步失败/超时 → 返回 None，
    由调用方降级到 LangChain / pypdf。
    ``transport`` 仅供测试注入 ``httpx.MockTransport``。
    """
    path = Path(path)
    base = mineru_base_url(config)
    if base is None:
        return None
    timeout = float(getattr(config, "document_mineru_timeout", 300) or 300)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10),
            transport=transport,
        ) as client:
            batch_id, file_url = _request_batch_urls(client, base, path, config)
            _upload_file(client, file_url, path)
            full_zip_url = _poll_batch_result(client, base, batch_id, config, timeout)
            return _download_markdown(client, full_zip_url)
    except Exception:
        logger.warning("MinerU parse_pdf failed for %s, fallback to LangChain", path, exc_info=True)
        return None


__all__ = [
    "MinerUError",
    "mineru_agent_base_url",
    "mineru_base_url",
    "parse_pdf",
    "parse_pdf_agent",
]
