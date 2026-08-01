"""VisionService — 图片下载 + Ollama 视觉推理（qwen3-vl）。

图片以公网 URL（如腾讯多媒体 CDN）到达，Ollama 的 images 参数只收 base64，
因此先下载 → base64 → POST {base_url}/api/generate 生成描述。
失败不抛出：describe 返回 ""（调用方降级为 [图片] 占位符）。
"""

import asyncio
import base64
import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# 轻量、中文友好的图片描述提示词
VISION_PROMPT = "请用中文简要描述这张图片的内容。"

# 单张图片体积上限（字节），防止恶意超大响应拖垮内存/带宽
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _is_blocked_ip(ip) -> bool:
    """SSRF 防护：阻断私网/环回/链路本地/组播/未指定地址。"""
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_unspecified
    )


class VisionService:
    """下载图片并调用本地 Ollama 视觉模型生成中文描述。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        prompt: str = VISION_PROMPT,
        timeout: float = 60.0,
        max_images: int = 3,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.prompt = prompt
        self.timeout = timeout
        self.max_images = max_images
        self._owns_http = http is None
        # 推理保留 timeout 预算，连接阶段单独缩短到 10s（防被慢速连接卡死）
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10))

    async def describe(self, src: str) -> str:
        """下载一张图并返回描述；失败返回空串（不抛出）。"""
        try:
            image_b64 = await self._download_base64(src)
            if not image_b64:
                return ""
            return await self._ollama_generate(image_b64)
        except Exception:
            logger.warning("Vision describe failed for %s", src, exc_info=True)
            return ""

    async def describe_many(self, srcs: list[str]) -> list[str]:
        """并行描述，最多 max_images 张；单张失败返回 ""。"""
        srcs = srcs[: self.max_images]
        if not srcs:
            return []
        return list(await asyncio.gather(*(self.describe(s) for s in srcs)))

    async def _host_is_blocked(self, host: str) -> bool:
        """解析 host 判断是否落在 SSRF 阻断地址段；解析失败按阻断处理。"""
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror:
            return True
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if _is_blocked_ip(ip):
                return True
        return False

    async def _download_base64(self, src: str) -> str:
        parsed = urlparse(src)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"unsupported image src: {src}")
        if await self._host_is_blocked(parsed.hostname):
            raise ValueError(f"blocked image host: {parsed.hostname}")
        resp = await self._http.get(src)
        resp.raise_for_status()
        if len(resp.content) > _MAX_IMAGE_BYTES:
            raise ValueError("image too large")
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype.startswith("text/"):
            raise ValueError(f"not an image: {ctype}")
        return base64.b64encode(resp.content).decode("ascii")

    async def _ollama_generate(self, image_b64: str) -> str:
        payload = {
            "model": self.model,
            "prompt": self.prompt,
            "images": [image_b64],
            "stream": False,
        }
        resp = await self._http.post(f"{self.base_url}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("response") or "").strip()

    async def close(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None
