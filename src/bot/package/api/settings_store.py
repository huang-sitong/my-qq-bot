"""设置持久化：读取 ``BotConfig``、写回 ``.env``。

设计说明
--------
项目配置单一数据源是 ``.env``（pydantic-settings + python-dotenv），
因此本模块直接复用 ``BotConfig`` 读取，并把 API 改动写回 ``.env``，
不引入 YAML 等第二套配置源。改动遵循项目约定：重启生效。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, find_dotenv

from bot.package.config import BotConfig

_ENV_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<sep>\s*=\s*)(?P<value>.*)$"
)


def _split_comment(value: str) -> tuple[str, str]:
    """把行尾 `` # 注释`` 从值里切出来（跳过引号内的 #）。"""
    in_single = in_double = False
    for i, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip(), value[i:]
    return value.rstrip(), ""


def _format_env_value(value: Any) -> str:
    """把 API 值格式化成可安全写回 .env 的字符串。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if value is None:
        return ""
    text = str(value)
    # 含空格 / # / 引号 / 空串时用双引号包裹，保证 dotenv 解析安全
    if text == "" or re.search(r"[\s#]", text) or '"' in text:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def field_env_map() -> dict[str, str]:
    """BotConfig 字段名 -> .env 变量名。"""
    return {
        name: str(field.validation_alias)
        for name, field in BotConfig.model_fields.items()
    }


def _config_from_raw(raw: dict[str, str]) -> BotConfig:
    """仅依据 .env 原始键值构造 ``BotConfig``。

    通过 init kwargs 传入（优先级最高），并显式 ``_env_file=None`` 关闭 dotenv
    源，从而**不受 os.environ 污染影响**——设置编辑器的语义就是"编辑 .env 里
    持久化的配置"，而不是环境变量覆盖后的生效值。pydantic 会自动完成类型转换
    与 ``model_validator`` 的回落逻辑（如 embed/vision 回落主 LLM）。
    """
    env_map = field_env_map()
    kwargs = {name: raw[alias] for name, alias in env_map.items() if alias in raw}
    return BotConfig(_env_file=None, **kwargs)


class SettingsStore:
    """读写 BotConfig 对应的 .env 持久化。"""

    def __init__(self, env_path: str | Path | None = None) -> None:
        self._env_path = Path(env_path) if env_path else self._default_env_path()

    @staticmethod
    def _default_env_path() -> Path:
        found = find_dotenv()
        return Path(found) if found else Path(".env")

    @property
    def env_path(self) -> Path:
        return self._env_path

    def load(self) -> BotConfig:
        """读取 .env 中持久化的配置（不读 os.environ，避免环境变量污染）。"""
        return _config_from_raw(self.read_raw_env())

    def read_raw_env(self) -> dict[str, str]:
        """读取 .env 原始键值（不含 os.environ）。"""
        if not self._env_path.exists():
            return {}
        return dict(dotenv_values(self._env_path))

    def validate(self, raw: dict[str, str]) -> BotConfig:
        """校验一组 .env 键值对应的配置，返回配置；不落盘。"""
        return _config_from_raw(raw)

    def write_env(self, raw: dict[str, str]) -> None:
        """把完整 env 字典写回 .env，尽量保留原行格式与行尾注释。"""
        self._env_path.parent.mkdir(parents=True, exist_ok=True)
        text = (
            self._env_path.read_text(encoding="utf-8")
            if self._env_path.exists()
            else ""
        )
        self._env_path.write_text(self._merge_text(text, raw), encoding="utf-8")

    @staticmethod
    def _merge_text(text: str, raw: dict[str, str]) -> str:
        lines = text.splitlines()
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            m = _ENV_LINE.match(line)
            if m:
                key = m.group("key")
                if key in raw:
                    seen.add(key)
                    new_value = raw[key]
                    if new_value == "":
                        # 空值表示删除该键（恢复默认）
                        continue
                    _value, comment = _split_comment(m.group("value"))
                    new_line = f"{m.group('indent')}{key}{m.group('sep')}{new_value}"
                    if comment:
                        new_line += "  " + comment
                    out.append(new_line)
                # key 不在目标字典里 -> 删除该行
                continue
            out.append(line)
        for key, value in raw.items():
            if key in seen or value == "":
                continue
            out.append(f"{key} = {value}")
        return "\n".join(out) + "\n"

    def update(self, updates: dict[str, Any]) -> BotConfig:
        """应用部分更新（字段名 -> 值），校验通过后写回 .env。

        - ``None`` 值表示清除该设置（删除 .env 中对应键，恢复默认）
        - 校验失败抛 ``pydantic.ValidationError``，不会写盘
        """
        env_map = field_env_map()
        merged = self.read_raw_env()
        for field, value in updates.items():
            if field not in env_map:
                raise KeyError(f"unknown setting field: {field}")
            env_key = env_map[field]
            if value is None:
                merged.pop(env_key, None)
            else:
                merged[env_key] = _format_env_value(value)
        self.validate(merged)
        self.write_env(merged)
        return self.load()


__all__ = ["SettingsStore", "field_env_map"]
