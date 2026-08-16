"""兼容层：Milvus 存储位于 ``knowledge.milvus``。"""
from knowledge.milvus import MilvusStore, _esc

__all__ = ["MilvusStore", "_esc"]
