"""配置接入点测试：LLM factory、重连策略、API 运行时 user id。"""

import asyncio

import pytest

import bot.core.llm as llm_module
import protocol.websocket.client as ws_client
from bot.core.llm import setup_llm
from common import BotConfig
from domain.satori.api import MESSAGE_CREATE, MessageCreateParams
from protocol.http.client import SatoriApiClient
from protocol.websocket.client import SatoriClient


class _FakeChatOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_setup_llm_reads_config(monkeypatch):
    monkeypatch.setattr(llm_module, "_llm", None)
    monkeypatch.setattr(llm_module, "ChatOpenAI", _FakeChatOpenAI)
    config = BotConfig(
        _env_file=None,
        llm_base_url="https://llm.example",
        llm_api_key="key",
        llm_model="model",
        llm_temperature=0.3,
        llm_max_retries=2,
        llm_request_timeout=10,
    )

    result = setup_llm(config)

    assert isinstance(result, _FakeChatOpenAI)
    assert result.kwargs == {
        "model": "model",
        "base_url": "https://llm.example",
        "api_key": "key",
        "temperature": 0.3,
        "max_retries": 2,
        "request_timeout": 10,
    }


def test_setup_llm_missing_credentials_raise(monkeypatch):
    monkeypatch.setattr(llm_module, "_llm", None)
    with pytest.raises(RuntimeError, match="BASE_URL"):
        setup_llm(BotConfig(_env_file=None, llm_base_url=None, llm_api_key=None))


def test_reconnect_delay_uses_config(monkeypatch):
    monkeypatch.setattr(ws_client.random, "uniform", lambda _a, _b: 0.0)
    client = SatoriClient(BotConfig(_env_file=None, max_reconnect_delay=3))

    assert client._reconnect_delay(1) == 2.0
    assert client._reconnect_delay(5) == 3.0


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {}


class _FakeHttp:
    def __init__(self) -> None:
        self.post_kwargs: dict | None = None

    async def post(self, path: str, json: dict, headers: dict) -> _FakeResponse:
        self.post_kwargs = {"path": path, "json": json, "headers": headers}
        return _FakeResponse()


def test_api_client_user_id_is_runtime_state():
    client = SatoriApiClient(BotConfig(_env_file=None, token="tok"))
    fake_http = _FakeHttp()
    client._http = fake_http
    client.set_user_id("bot-1")

    asyncio.run(
        client.call_api(
            MESSAGE_CREATE,
            MessageCreateParams(channel_id="c1", content="hi"),
        )
    )

    assert fake_http.post_kwargs is not None
    assert fake_http.post_kwargs["headers"]["Satori-User-ID"] == "bot-1"
