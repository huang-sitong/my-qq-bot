"""VisionService：图片下载 → base64 → Ollama /api/generate 生成描述。"""

import asyncio
import base64

import httpx

from bot.core.vision.service import VISION_PROMPT, _MAX_IMAGE_BYTES, VisionService

# 公网字面 IP：字面 IP 的 getaddrinfo 不查 DNS，避免测试慢/不稳，也不触发 SSRF 阻断
IMG = "http://1.2.3.4/download?appid=1&fileid=abc"
GEN = "http://localhost:11434/api/generate"


class FakeResponse:
    def __init__(self, status=200, content=b"", json_data=None, headers=None):
        self.status_code = status
        self.content = content
        self._json = json_data
        self.headers = headers if headers is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json


class FakeClient:
    """按 URL 返回预设响应的假 httpx.AsyncClient，记录请求。"""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []
        self.closed = False

    async def aclose(self):
        self.closed = True

    async def get(self, url, **kwargs):
        self.requests.append(("get", url))
        if url not in self.responses:
            raise httpx.HTTPError(f"no response for {url}")
        return self.responses[url]

    async def post(self, url, json=None, **kwargs):
        self.requests.append(("post", url, json))
        if url not in self.responses:
            raise httpx.HTTPError(f"no response for {url}")
        return self.responses[url]


def _svc(client, max_images=3):
    return VisionService(base_url="http://localhost:11434", model="qwen3-vl:2b",
                         http=client, max_images=max_images)


def test_describe_downloads_and_generates():
    png = b"\x89PNG\r\n\x1a\n"
    client = FakeClient({
        IMG: FakeResponse(content=png),
        GEN: FakeResponse(json_data={"response": "一只猫坐在窗台上"}),
    })
    svc = _svc(client)
    assert asyncio.run(svc.describe(IMG)) == "一只猫坐在窗台上"
    post = [r for r in client.requests if r[0] == "post"]
    assert len(post) == 1
    payload = post[0][2]
    assert payload["model"] == "qwen3-vl:2b"
    assert payload["stream"] is False
    assert payload["prompt"] == VISION_PROMPT
    assert payload["images"] == [base64.b64encode(png).decode("ascii")]


def test_describe_download_failure_returns_empty():
    svc = _svc(FakeClient({IMG: FakeResponse(status=403, content=b"")}))
    assert asyncio.run(svc.describe(IMG)) == ""


def test_describe_ollama_failure_returns_empty():
    svc = _svc(FakeClient({
        IMG: FakeResponse(content=b"data"),
        GEN: FakeResponse(status=500),
    }))
    assert asyncio.run(svc.describe(IMG)) == ""


def test_describe_missing_src_returns_empty():
    svc = _svc(FakeClient({}))
    assert asyncio.run(svc.describe(IMG)) == ""


def test_describe_many_caps_at_max_images():
    client = FakeClient({
        f"{IMG}1": FakeResponse(content=b"a"),
        f"{IMG}2": FakeResponse(content=b"b"),
        f"{IMG}3": FakeResponse(content=b"c"),
        GEN: FakeResponse(json_data={"response": "图"}),
    })
    svc = _svc(client, max_images=2)
    assert asyncio.run(svc.describe_many([f"{IMG}1", f"{IMG}2", f"{IMG}3"])) == ["图", "图"]


def test_describe_many_partial_failure():
    client = FakeClient({
        f"{IMG}1": FakeResponse(content=b"a"),
        GEN: FakeResponse(json_data={"response": "图"}),
    })
    svc = _svc(client)
    assert asyncio.run(svc.describe_many([f"{IMG}1", f"{IMG}2"])) == ["图", ""]


def test_describe_blocks_private_host():
    svc = _svc(FakeClient({}))
    for url in ("http://127.0.0.1/x.png", "http://10.0.0.1/x.png",
                "http://169.254.169.254/latest/meta-data"):
        assert asyncio.run(svc.describe(url)) == ""


def test_describe_blocks_non_http_scheme():
    svc = _svc(FakeClient({}))
    for url in ("file:///etc/passwd", "ftp://1.2.3.4/x.png"):
        assert asyncio.run(svc.describe(url)) == ""


def test_describe_rejects_oversized_response():
    svc = _svc(FakeClient({IMG: FakeResponse(content=b"x" * (_MAX_IMAGE_BYTES + 1))}))
    assert asyncio.run(svc.describe(IMG)) == ""


def test_describe_rejects_html_content_type():
    svc = _svc(FakeClient({
        IMG: FakeResponse(content=b"<html>", headers={"content-type": "text/html"}),
    }))
    assert asyncio.run(svc.describe(IMG)) == ""


def test_close_closes_owned_client():
    svc = VisionService(base_url="http://localhost:11434", model="qwen3-vl:2b")
    assert svc._owns_http
    assert svc._http is not None
    asyncio.run(svc.close())
    assert svc._http is None


def test_close_does_not_close_injected_client():
    client = FakeClient({})
    svc = _svc(client)
    asyncio.run(svc.close())
    assert client.closed is False
