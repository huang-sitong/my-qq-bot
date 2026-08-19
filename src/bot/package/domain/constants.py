"""跨模块共享常量。"""

# 图外 aupdate_state 必须显式指定写入节点；describe_image 是消息进入图后的
# 第一个状态写入节点，连续外部更新时不会让 LangGraph 出现 Ambiguous update。
EXTERNAL_UPDATE_NODE = "describe_image"

__all__ = ["DIRECT_CHANNEL_TYPE", "EXTERNAL_UPDATE_NODE"]

# Satori ChannelType.DIRECT 的整数值；bot.utils.routing 不应反向依赖 platform 包，
# 因此在此保存协议共享常量。
DIRECT_CHANNEL_TYPE = 1
