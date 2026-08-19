"""SatoriApiClient.send_file：群文件、私聊文件、图片消息三条路径。"""

import asyncio
import json

import httpx

from bot.package.config import BotConfig
from bot.package.platform.satori.http import SatoriApiClient


def _client_with_transport(handler) -> SatoriApiClient:
    client = SatoriApiClient(BotConfig(
        _env_file=None,
        api_base_url="http://satori.test",
        api_platform="qq",
        token="secret",
    ))
    client._http = httpx.AsyncClient(
        base_url="http://satori.test",
        transport=httpx.MockTransport(handler),
    )
    client._onebot11_http = httpx.AsyncClient(
        base_url="http://onebot.test",
        transport=httpx.MockTransport(handler),
    )
    return client


def test_send_file_group_uses_internal_upload(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, json={"status": "ok", "retcode": 0})

    client = _client_with_transport(handler)
    path = tmp_path / "album.zip"
    path.write_bytes(b"zip")

    async def run():
        try:
            return await client.send_file("796219047", str(path), "album.zip")
        finally:
            await client.close()

    result = asyncio.run(run())
    assert result["status"] == "ok"
    assert seen["url"] == "http://onebot.test/upload_group_file"
    assert seen["body"] == {
        "group_id": "796219047",
        "file": str(path.resolve()),
        "name": "album.zip",
    }


def test_send_file_private_uses_internal_upload(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "ok", "retcode": 0})

    client = _client_with_transport(handler)
    path = tmp_path / "chapter.pdf"
    path.write_bytes(b"pdf")

    async def run():
        try:
            return await client.send_file("private:10001", str(path), "chapter.pdf")
        finally:
            await client.close()

    asyncio.run(run())
    assert seen["url"] == "http://onebot.test/upload_private_file"
    assert seen["body"] == {
        "user_id": "10001",
        "file": str(path.resolve()),
        "name": "chapter.pdf",
    }


def test_send_file_image_uses_message_create(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"id": "m1"}])

    client = _client_with_transport(handler)
    path = tmp_path / "page.png"
    path.write_bytes(b"png")

    async def run():
        try:
            return await client.send_file("796219047", str(path), "page.png")
        finally:
            await client.close()

    asyncio.run(run())
    assert seen["url"].endswith("/v1/message.create")
    content = seen["body"]["content"]
    assert content.startswith("<img src=")
    assert "title=" in content
    assert seen["body"]["channel_id"] == "796219047"


def test_send_file_rejects_missing_or_path_like_name(tmp_path):
    client = _client_with_transport(lambda request: httpx.Response(500))

    async def run():
        try:
            await client.send_file("g1", str(tmp_path / "missing.txt"))
            assert False, "missing file should raise"
        except ValueError as exc:
            assert "不存在" in str(exc)

        path = tmp_path / "a.txt"
        path.write_text("x", encoding="utf-8")
        try:
            await client.send_file("g1", str(path), "dir/a.txt")
            assert False, "path-like name should raise"
        except ValueError as exc:
            assert "不能包含" in str(exc)
        finally:
            await client.close()

    asyncio.run(run())


def test_onebot11_client_uses_configured_timeout():
    client = SatoriApiClient(BotConfig(
        _env_file=None,
        onebot11_api_base_url="http://onebot.test",
        onebot11_timeout=42,
    ))
    try:
        assert client.onebot11_http.timeout.read == 42
        assert client.onebot11_http.timeout.connect == 10
    finally:
        asyncio.run(client.close())
