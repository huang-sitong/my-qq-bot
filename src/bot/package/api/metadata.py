"""设置项元数据：分组、中文说明、是否敏感。

key 为 ``BotConfig`` 的字段名（也是前端设置表单的 key），
env 为对应的 .env 变量名（见 ``BotConfig.model_fields[*].validation_alias``）。
"""

from __future__ import annotations

from bot.package.config import BotConfig

# (group, label, secret)
FIELDS: dict[str, tuple[str, str, bool]] = {
    # --- Transport ---
    "ws_url": ("传输", "Satori WebSocket 地址", False),
    "token": ("传输", "Satori 访问令牌", True),
    "reconnect": ("传输", "断线自动重连", False),
    "max_reconnect_delay": ("传输", "最大重连延迟(秒)", False),
    "api_base_url": ("传输", "Satori API 基地址", False),
    "onebot11_api_base_url": ("传输", "OneBot11 API 基地址", False),
    "onebot11_timeout": ("传输", "OneBot11 请求超时(秒)", False),
    "api_platform": ("传输", "API 平台", False),
    # --- Message concurrency ---
    "message_worker_count": ("消息并发", "消息 Worker 数量", False),
    "message_queue_maxsize": ("消息并发", "消息队列上限(0=无界)", False),
    "message_batch_max": ("消息并发", "同会话突发消息合并上限", False),
    "message_dedup_size": ("消息并发", "event_id 去重窗口(0=关闭)", False),
    # --- Graph ---
    "graph_recursion_limit": ("图编排", "LangGraph 节点执行上限", False),
    # --- LLM ---
    "llm_base_url": ("LLM", "LLM Base URL", False),
    "llm_api_key": ("LLM", "LLM API Key", True),
    "llm_model": ("LLM", "LLM 模型名", False),
    "llm_temperature": ("LLM", "采样温度(0-2)", False),
    "llm_max_retries": ("LLM", "最大重试次数", False),
    "llm_request_timeout": ("LLM", "请求超时(秒)", False),
    "llm_multimodal": ("LLM", "主 LLM 多模态", False),
    "llm_parallel_tool_calls": ("LLM", "单轮并行多工具调用", False),
    "llm_context_window": ("LLM", "上下文窗口大小", False),
    # --- Summary ---
    "summary_trigger_ratio": ("摘要压缩", "自动压缩触发比例", False),
    "summary_keep_ratio": ("摘要压缩", "压缩保留比例", False),
    "summary_max_input_tokens": ("摘要压缩", "摘要最大输入 Token", False),
    # --- Storage / Persona ---
    "db_dir": ("存储/人设", "数据库目录", False),
    "persona_prompt": ("存储/人设", "人设 Prompt", False),
    # --- RAG / Embedding ---
    "rag_enabled": ("RAG/嵌入", "启用 RAG", False),
    "embed_model": ("RAG/嵌入", "嵌入模型名", False),
    "embed_base_url": ("RAG/嵌入", "嵌入 Base URL", False),
    "embed_api_key": ("RAG/嵌入", "嵌入 API Key", True),
    "embed_dimensions": ("RAG/嵌入", "嵌入维度", False),
    "embed_cache_enabled": ("RAG/嵌入", "启用嵌入缓存", False),
    "embed_cache_max_entries": ("RAG/嵌入", "嵌入缓存上限", False),
    "rag_top_k": ("RAG/嵌入", "检索 Top-K", False),
    "rag_score_threshold": ("RAG/嵌入", "检索分数阈值", False),
    "rag_retention_per_thread": ("RAG/嵌入", "每会话保留条数", False),
    "rag_max_agent_rounds": ("RAG/嵌入", "RAG 最大工具轮数", False),
    # --- Document Ingestion ---
    "document_collection": ("文档导入", "文档集合名", False),
    "document_mineru_endpoint": ("文档导入", "MinerU 精准解析端点", False),
    "document_mineru_api_key": ("文档导入", "MinerU API Key", True),
    "document_mineru_agent_enabled": ("文档导入", "启用 MinerU Agent 解析", False),
    "document_mineru_timeout": ("文档导入", "MinerU 超时(秒)", False),
    "document_chunk_size": ("文档导入", "文档切分块大小", False),
    "document_chunk_overlap": ("文档导入", "文档切分重叠", False),
    # --- Vision ---
    "vision_enabled": ("视觉", "启用视觉服务", False),
    "vision_model": ("视觉", "视觉模型名", False),
    "vision_base_url": ("视觉", "视觉 Base URL", False),
    "vision_api_key": ("视觉", "视觉 API Key", True),
    "vision_max_images": ("视觉", "单轮最多图片数", False),
    "vision_timeout": ("视觉", "视觉请求超时(秒)", False),
    # --- MCP ---
    "mcp_enabled": ("MCP", "启用 MCP 外部工具", False),
    "mcp_servers_file": ("MCP", "MCP server 定义文件", False),
    "mcp_tool_name_prefix": ("MCP", "MCP 工具名加前缀", False),
    # --- Skills ---
    "skills_enabled": ("技能", "启用技能系统", False),
    "skills_dir": ("技能", "技能目录", False),
    "skills_index_max": ("技能", "技能索引上限", False),
    "skills_allowlist": ("技能", "技能白名单(逗号分隔)", False),
    "skills_denylist": ("技能", "技能黑名单(逗号分隔)", False),
    # --- Tools ---
    "tools_allowlist": ("工具", "工具白名单(逗号分隔)", False),
    "tools_denylist": ("工具", "工具黑名单(逗号分隔)", False),
    # --- Commands ---
    "command_enabled": ("指令", "启用斜杠指令", False),
    "command_prefix": ("指令", "指令前缀", False),
    "admin_ids": ("指令", "管理员 ID(逗号分隔)", False),
    # --- Reply behavior ---
    "auto_reply": ("回复行为", "群聊自动回复", False),
    "auto_reply_random_rate": ("回复行为", "自动回复概率(0-1)", False),
    "auto_reply_cooldown": ("回复行为", "自动回复冷却(秒)", False),
    # --- Bash ---
    "bash_enabled": ("Bash", "启用 run_bash 工具", False),
    "bash_shell": ("Bash", "Bash Shell 路径", False),
    "bash_timeout": ("Bash", "Bash 超时(秒)", False),
    "bash_max_output": ("Bash", "Bash 输出截断字符数", False),
    "bash_allowed_roots": ("Bash", "额外白名单根目录(逗号分隔)", False),
}

SECRET_FIELDS = frozenset(
    field for field, (_g, _l, secret) in FIELDS.items() if secret
)

# 确保元数据覆盖 BotConfig 全部字段（新增字段时提醒补全）
_MISSING = set(BotConfig.model_fields) - set(FIELDS)
if _MISSING:
    raise RuntimeError(f"settings metadata missing fields: {sorted(_MISSING)}")

DEFAULT_GROUP = "其他"
DEFAULT_LABEL = ""


def metadata_for(field_name: str) -> tuple[str, str, bool]:
    """返回 (group, label, secret)；未知字段给兜底值。"""
    return FIELDS.get(field_name, (DEFAULT_GROUP, DEFAULT_LABEL, False))
