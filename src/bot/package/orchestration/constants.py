"""编排层常量 — 图外 aupdate_state 写入节点等。"""

# 图外 aupdate_state 必须显式指定写入节点；describe_image 是消息进入图后的
# 第一个状态写入节点，连续外部更新时不会让 LangGraph 出现 Ambiguous update。
EXTERNAL_UPDATE_NODE = "describe_image"

__all__ = ["EXTERNAL_UPDATE_NODE"]
