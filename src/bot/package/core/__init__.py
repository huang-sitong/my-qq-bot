"""应用核心包：装配、生命周期与基础工厂。"""

from .database import DatabaseManager
from .llm import setup_llm

__all__ = ["DatabaseManager", "setup_llm"]
