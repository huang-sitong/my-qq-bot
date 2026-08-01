"""VisionService — 图片下载 + Ollama 视觉推理（qwen3-vl）。

图片以公网 URL（如腾讯多媒体 CDN）到达，Ollama 的 images 参数只收 base64，
因此先下载 → base64 → POST {base_url}/api/generate 生成描述。
失败不抛出：describe 返回 ""（调用方降级为 [图片] 占位符）。
"""

import base64
import logging

import httpx

logger = logging.getLogger(__name__)

# 轻量、中文友好的图片描述提示词
VISION_PROMPT = "请用中文简要描述这张图片的内容。"


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
        self._http = http or httpx.AsyncClient(timeout=timeout)

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
        """逐个描述，最多 max_images 张；单张失败返回 ""。"""
        descs = []
        for src in srcs[: self.max_images]:
            descs.append(await self.describe(src))
        return descs

    async def _download_base64(self, src: str) -> str:
        resp = await self._http.get(src)
        resp.raise_for_status()
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
