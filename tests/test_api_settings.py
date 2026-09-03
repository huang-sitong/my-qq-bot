"""控制台后端设置 API 测试：SettingsStore 持久化 + FastAPI 路由。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bot.package.api import create_api_app
from bot.package.api.router import _get_store
from bot.package.api.settings_store import SettingsStore, field_env_map


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text(
        "# header comment\n"
        "BOT_LLM_MODEL = deepseek-v4-flash   # 主模型\n"
        "BOT_LLM_TEMPERATURE = 0.7\n"
        "BOT_RAG_ENABLED = 1\n"
        "API_KEY = sk-secret\n"
        "SOME_EXTRA = keepme\n",
        encoding="utf-8",
    )
    return env


def test_store_update_preserves_comments_and_extra_keys(env_file: Path):
    store = SettingsStore(env_file)
    cfg = store.update(
        {"llm_model": "gpt-4o", "llm_temperature": 0.8, "rag_enabled": False}
    )
    assert cfg.llm_model == "gpt-4o"
    assert cfg.llm_temperature == 0.8
    assert cfg.rag_enabled is False

    text = env_file.read_text(encoding="utf-8")
    assert "BOT_LLM_MODEL = gpt-4o  # 主模型" in text
    assert "BOT_LLM_TEMPERATURE = 0.8" in text
    assert "BOT_RAG_ENABLED = 0" in text
    assert "SOME_EXTRA = keepme" in text
    assert "# header comment" in text


def test_store_clear_removes_key_and_restores_default(env_file: Path):
    store = SettingsStore(env_file)
    store.update({"llm_model": None})
    assert store.load().llm_model == "deepseek-v4-flash"
    text = env_file.read_text(encoding="utf-8")
    assert "BOT_LLM_MODEL" not in text


def test_store_invalid_update_does_not_write(env_file: Path):
    store = SettingsStore(env_file)
    with pytest.raises(ValidationError):
        store.update({"llm_temperature": "abc"})
    # 校验失败不应落盘
    text = env_file.read_text(encoding="utf-8")
    assert "BOT_LLM_TEMPERATURE = 0.7" in text


def test_store_unknown_field_rejected(env_file: Path):
    store = SettingsStore(env_file)
    with pytest.raises(KeyError):
        store.update({"not_a_field": 1})


def test_format_bool_and_list_values(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    store = SettingsStore(env)
    store.update(
        {
            "auto_reply": True,
            "message_worker_count": 4,
            "admin_ids": ["u1", "u2"],
            "llm_temperature": 0.5,
        }
    )
    text = env.read_text(encoding="utf-8")
    assert "BOT_AUTO_REPLY = 1" in text
    assert "BOT_MESSAGE_WORKER_COUNT = 4" in text
    assert "BOT_ADMIN_IDS = u1, u2" in text
    assert "BOT_LLM_TEMPERATURE = 0.5" in text


def test_field_env_map_covers_all_config_fields():
    from bot.package.config import BotConfig

    assert set(field_env_map()) == set(BotConfig.model_fields)
    assert field_env_map()["llm_model"] == "BOT_LLM_MODEL"


def _client_with_env(env_file: Path) -> TestClient:
    app = create_api_app()
    store = SettingsStore(env_file)
    app.dependency_overrides[_get_store] = lambda: store
    return TestClient(app)


def test_get_settings_shape_and_secret_masking(env_file: Path):
    client = _client_with_env(env_file)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 72
    items = {it["key"]: it for it in data["settings"]}
    assert items["llm_model"]["value"] == "deepseek-v4-flash"
    assert items["llm_model"]["env"] == "BOT_LLM_MODEL"
    assert items["llm_model"]["type"] == "string"
    assert items["llm_model"]["group"] == "LLM"
    assert items["llm_model"]["secret"] is False
    # 敏感字段应被掩码
    assert items["llm_api_key"]["secret"] is True
    assert items["llm_api_key"]["value"] == "***"


def test_put_settings_updates_and_persists(env_file: Path):
    client = _client_with_env(env_file)
    resp = client.put("/api/settings", json={"llm_model": "gpt-4o", "llm_temperature": 0.9})
    assert resp.status_code == 200
    items = {it["key"]: it for it in resp.json()["settings"]}
    assert items["llm_model"]["value"] == "gpt-4o"
    assert items["llm_temperature"]["value"] == 0.9
    assert "BOT_LLM_MODEL = gpt-4o" in env_file.read_text(encoding="utf-8")


def test_put_settings_unknown_field_returns_400(env_file: Path):
    client = _client_with_env(env_file)
    resp = client.put("/api/settings", json={"not_a_field": 1})
    assert resp.status_code == 400
    assert "未知设置项" in resp.json()["detail"]


def test_put_settings_invalid_value_returns_422(env_file: Path):
    client = _client_with_env(env_file)
    resp = client.put("/api/settings", json={"llm_temperature": "abc"})
    assert resp.status_code == 422


def test_put_secret_mask_placeholder_is_noop(env_file: Path):
    client = _client_with_env(env_file)
    resp = client.put("/api/settings", json={"llm_api_key": "***"})
    assert resp.status_code == 200
    # 占位符不应写入 .env
    assert "API_KEY = ***" not in env_file.read_text(encoding="utf-8")


def test_health_endpoint(env_file: Path):
    client = _client_with_env(env_file)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
