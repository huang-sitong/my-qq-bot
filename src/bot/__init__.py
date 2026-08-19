"""bot 应用包。

应用主体位于 ``bot.package``；本文件保持轻量，不重导出任何子包，避免
import bot 时拉起 langchain / milvus / aiosqlite 等重型依赖。
"""

__all__: list[str] = []
