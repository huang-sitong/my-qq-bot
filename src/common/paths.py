"""Repository-root paths shared by runtime modules."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

__all__ = ["PROJECT_ROOT"]
