"""设置项 GET / PUT 路由。"""

from __future__ import annotations

import json
import types
import typing
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from bot.package.config import BotConfig

from .metadata import SECRET_FIELDS, metadata_for
from .schemas import SettingItem, SettingsResponse, SettingsUpdate
from .settings_store import SettingsStore

router = APIRouter(prefix="/api", tags=["settings"])

# 前端把敏感字段原样回传的占位符：视为"不修改"
_MASK_PLACEHOLDER = "***"


def _get_store() -> SettingsStore:
    return SettingsStore()


StoreDep = Annotated[SettingsStore, Depends(_get_store)]
UpdateBody = Annotated[SettingsUpdate, Body()]


def _field_type(annotation: Any) -> str:
    args = typing.get_args(annotation)
    origin = typing.get_origin(annotation)
    if args:
        if origin is list:
            return "list"
        if origin in (typing.Union, types.UnionType):
            non_none = [a for a in args if a is not type(None)]
            if non_none:
                return _field_type(non_none[0])
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    return "string"


def _serialize(config: BotConfig, env_path: str) -> SettingsResponse:
    items: list[SettingItem] = []
    for field_name, field in BotConfig.model_fields.items():
        group, label, secret = metadata_for(field_name)
        value = getattr(config, field_name)
        has_value = value is not None and value != "" and value != [] and value is not False
        display: Any = value
        if secret and has_value:
            display = _MASK_PLACEHOLDER
        items.append(
            SettingItem(
                key=field_name,
                env=str(field.validation_alias),
                value=display,
                type=_field_type(field.annotation),
                group=group,
                label=label,
                secret=secret,
                has_value=has_value,
            )
        )
    return SettingsResponse(settings=items, loaded_from=env_path, count=len(items))


@router.get("/settings", response_model=SettingsResponse)
def get_settings(store: StoreDep) -> SettingsResponse:
    return _serialize(store.load(), str(store.env_path))


@router.put("/settings", response_model=SettingsResponse)
def update_settings(body: UpdateBody, store: StoreDep) -> SettingsResponse:
    payload = body.updates()
    unknown = set(payload) - set(BotConfig.model_fields)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"未知设置项: {', '.join(sorted(unknown))}",
        )
    # 敏感字段若只是把掩码占位符原样回传，视为不修改
    updates = {
        key: value
        for key, value in payload.items()
        if not (key in SECRET_FIELDS and value == _MASK_PLACEHOLDER)
    }
    if not updates:
        return _serialize(store.load(), str(store.env_path))
    try:
        config = store.update(updates)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
    return _serialize(config, str(store.env_path))


__all__ = ["router"]
