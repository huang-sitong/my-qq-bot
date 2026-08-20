"""知识层提示词 — 嵌入检索任务前缀。"""

# 嵌入检索任务前缀（EmbeddingService 用，Query 与 Document 共用保持向量空间一致）
RETRIEVAL_TASK = "检索群聊历史中与问题最相关的消息"

__all__ = ["RETRIEVAL_TASK"]
