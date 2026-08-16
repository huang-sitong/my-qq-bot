"""兼容层：RAG 服务位于 ``knowledge.service``。"""
from knowledge.service import TS_FMT, RagService, normalize_time

__all__ = ["TS_FMT", "RagService", "normalize_time"]
