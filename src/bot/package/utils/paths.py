"""Repository-root paths shared by runtime modules."""

from pathlib import Path

# src/bot/package/utils/paths.py -> project root 上溯 4 级
PROJECT_ROOT = Path(__file__).resolve().parents[4]

__all__ = ["PROJECT_ROOT"]
