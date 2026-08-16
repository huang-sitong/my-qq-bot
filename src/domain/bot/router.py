"""兼容层：路由领域对象已迁移到 ``conversation.router``。"""
from conversation.router import RouteAction, RouteDecision

__all__ = ["RouteAction", "RouteDecision"]
