from typing import Annotated

from dotenv import find_dotenv
from pydantic import BeforeValidator, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .prompts import DEFAULT_PERSONA_PROMPT


def _parse_flag(value: object) -> bool:
    """保留旧 BotConfig 的 0/1/false/true/空串布尔语义。"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _parse_comma_list(value: object) -> list[str]:
    """逗号分隔字符串 → 保序去重列表；list/tuple 原样规范化；其余返回 []。"""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        return []
    return list(dict.fromkeys(parts))


Flag = Annotated[bool, BeforeValidator(_parse_flag)]


# 旧 BotConfig 用 load_dotenv() 从模块目录向上搜索 .env；pydantic-settings 的
# env_file=".env" 只认 CWD 相对路径，从子目录启动会静默丢全部配置。这里在
# import 时用 find_dotenv 解析一次绝对路径（common/ 向上找），修复该回归且
# 不污染进程环境表（测试仍可用 _env_file=None 关掉 dotenv 源）。
_ENV_FILE = find_dotenv() or None


class BotConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- Transport ---
    ws_url: str = Field(
        default="ws://localhost:5600/v1/events",
        validation_alias="BOT_WS_URL",
    )
    token: str | None = Field(default=None, validation_alias="BOT_TOKEN")
    reconnect: Flag = Field(default=True, validation_alias="BOT_RECONNECT")
    max_reconnect_delay: int = Field(
        default=30,
        gt=0,
        validation_alias="BOT_MAX_RECONNECT_DELAY",
    )
    api_base_url: str = Field(
        default="http://localhost:5600",
        validation_alias="BOT_API_BASE_URL",
    )
    onebot11_api_base_url: str = Field(
        default="http://localhost:3000",
        validation_alias="BOT_ONEBOT11_API_BASE_URL",
    )
    onebot11_timeout: int = Field(
        default=60,
        gt=0,
        validation_alias="BOT_ONEBOT11_TIMEOUT",
    )
    api_platform: str = Field(
        default="llonebot",
        validation_alias="BOT_API_PLATFORM",
    )

    # --- Message concurrency ---
    message_worker_count: int = Field(
        default=1,
        ge=1,
        le=64,
        validation_alias="BOT_MESSAGE_WORKER_COUNT",
    )
    message_queue_maxsize: int = Field(
        default=0,
        ge=0,
        validation_alias="BOT_MESSAGE_QUEUE_MAXSIZE",
    )
    # 同 thread 突发消息合并批处理上限：worker 取到一条消息后，机会式把队列里
    # 紧随其后的同会话消息并入同一批（一次图调用/一次回复）。0 或 1 = 关闭合并，
    # 维持逐条处理；负载越高队列积压越多，合并收益越大。
    message_batch_max: int = Field(
        default=4,
        ge=0,
        validation_alias="BOT_MESSAGE_BATCH_MAX",
    )

    # --- Graph ---
    # LangGraph 总节点执行上限（call_llm/tools/skill_manager 等每次执行都计 1）。
    # 默认 128 远大于当前 rag_max_agent_rounds=12 的正常工具回环开销。
    graph_recursion_limit: int = Field(
        default=128,
        gt=0,
        validation_alias="BOT_GRAPH_RECURSION_LIMIT",
    )

    # --- LLM ---
    llm_base_url: str | None = Field(default=None, validation_alias="BASE_URL")
    llm_api_key: str | None = Field(default=None, validation_alias="API_KEY")
    llm_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias="BOT_LLM_MODEL",
    )
    llm_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        validation_alias="BOT_LLM_TEMPERATURE",
    )
    llm_max_retries: int = Field(
        default=1,
        ge=0,
        validation_alias="BOT_LLM_MAX_RETRIES",
    )
    llm_request_timeout: int = Field(
        default=30,
        gt=0,
        validation_alias="BOT_LLM_REQUEST_TIMEOUT",
    )
    # 主 LLM 是否多模态（env BOT_LLM_MULTIMODAL，默认 0）：
    # 1 → 图片直接进主 LLM（describe_image 不预描述，视觉服务仅作 RAG 索引）；
    # 0 → 图片走视觉服务描述（纯文本 LLM 兜底）。失败方向永远偏纯文本——
    # 设错 0 只是退回现状；设错 1 会把图片块塞给不支持图片的 API。
    llm_multimodal: Flag = Field(
        default=False,
        validation_alias="BOT_LLM_MULTIMODAL",
    )

    # --- Context Window ---
    llm_context_window: int = Field(
        default=500_000,
        gt=0,
        validation_alias="BOT_LLM_CONTEXT_WINDOW",
    )
    summary_trigger_ratio: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        validation_alias="BOT_SUMMARY_TRIGGER_RATIO",
    )
    summary_keep_ratio: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        validation_alias="BOT_SUMMARY_KEEP_RATIO",
    )
    summary_max_input_tokens: int = Field(
        default=8_000,
        ge=0,
        validation_alias="BOT_SUMMARY_MAX_INPUT_TOKENS",
    )

    # --- Storage ---
    db_dir: str = Field(default="db", validation_alias="BOT_DB_DIR")

    # --- Persona ---
    persona_prompt: str = Field(
        default=DEFAULT_PERSONA_PROMPT,
        validation_alias="BOT_PERSONA_PROMPT",
    )

    # --- RAG (群聊历史向量检索) ---
    rag_enabled: Flag = Field(
        default=True,
        validation_alias="BOT_RAG_ENABLED",
    )
    embed_model: str = Field(
        default="qwen3-embedding:0.6b",
        validation_alias="BOT_EMBED_MODEL",
    )
    # 嵌入与视觉各自独立 URL：embed/vision 未设置时回落主 LLM BASE_URL/API_KEY。
    embed_base_url: str | None = Field(
        default=None,
        validation_alias="BOT_EMBED_BASE_URL",
    )
    # OpenAI 兼容嵌入 API key；未设时回落主 LLM 的 API_KEY（同供应商零配置）
    embed_api_key: str | None = Field(
        default=None,
        validation_alias="BOT_EMBED_API_KEY",
    )
    vision_base_url: str | None = Field(
        default=None,
        validation_alias="BOT_VISION_BASE_URL",
    )
    embed_dimensions: int = Field(
        default=1024,
        gt=0,
        validation_alias="BOT_EMBED_DIMENSIONS",
    )
    embed_cache_enabled: Flag = Field(
        default=True,
        validation_alias="BOT_EMBED_CACHE_ENABLED",
    )
    embed_cache_max_entries: int = Field(
        default=20_000,
        ge=0,
        validation_alias="BOT_EMBED_CACHE_MAX_ENTRIES",
    )
    rag_top_k: int = Field(
        default=5,
        gt=0,
        validation_alias="BOT_RAG_TOP_K",
    )
    rag_score_threshold: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        validation_alias="BOT_RAG_SCORE_THRESHOLD",
    )
    rag_retention_per_thread: int = Field(
        default=2000,
        gt=0,
        validation_alias="BOT_RAG_RETENTION_PER_THREAD",
    )
    rag_max_agent_rounds: int = Field(
        default=12,
        ge=0,
        validation_alias="BOT_RAG_MAX_AGENT_ROUNDS",
    )

    # --- Vision (OpenAI 兼容视觉 API，图片描述) ---
    vision_enabled: Flag = Field(
        default=True,
        validation_alias="BOT_VISION_ENABLED",
    )
    vision_model: str = Field(
        default="qwen3-vl:2b",
        validation_alias="BOT_VISION_MODEL",
    )
    # OpenAI 兼容视觉 API key；未设时回落主 LLM 的 API_KEY（同供应商零配置）
    vision_api_key: str | None = Field(
        default=None,
        validation_alias="BOT_VISION_API_KEY",
    )
    vision_max_images: int = Field(
        default=3,
        ge=0,
        validation_alias="BOT_VISION_MAX_IMAGES",
    )
    vision_timeout: int = Field(
        default=60,
        gt=0,
        validation_alias="BOT_VISION_TIMEOUT",
    )

    # --- MCP (外部工具，经 langchain-mcp-adapters 接入) ---
    mcp_enabled: Flag = Field(
        default=False,
        validation_alias="BOT_MCP_ENABLED",
    )
    # server 定义集中在 config/mcp_servers.json（可提交、可评审），
    # 密钥用 ${ENV_VAR} 占位插值；本字段只保存文件路径。
    mcp_servers_file: str = Field(
        default="config/mcp_servers.json",
        validation_alias="BOT_MCP_SERVERS_FILE",
    )
    mcp_tool_name_prefix: Flag = Field(
        default=False,
        validation_alias="BOT_MCP_TOOL_NAME_PREFIX",
    )

    # --- Skills（提示词包技能，按需加载正文） ---
    skills_enabled: Flag = Field(
        default=True,
        validation_alias="BOT_SKILLS_ENABLED",
    )
    skills_dir: str = Field(
        default="skills",
        validation_alias="BOT_SKILLS_DIR",
    )
    skills_index_max: int = Field(
        default=50,
        ge=0,
        validation_alias="BOT_SKILLS_INDEX_MAX",
    )

    # --- Commands（图外斜杠指令模块） ---
    command_enabled: Flag = Field(
        default=True,
        validation_alias="BOT_COMMAND_ENABLED",
    )
    command_prefix: str = Field(
        default="/",
        min_length=1,
        validation_alias="BOT_COMMAND_PREFIX",
    )
    # NoDecode keeps BOT_ADMIN_IDS raw; pydantic-settings would otherwise try
    # to JSON-decode "u1, u2" before _parse_admin_ids can run.
    admin_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias="BOT_ADMIN_IDS",
    )

    # --- Reply behavior（回复行为；命令可运行时改写） ---
    auto_reply: Flag = Field(
        default=False,
        validation_alias="BOT_AUTO_REPLY",
    )
    auto_reply_random_rate: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        validation_alias="BOT_AUTO_REPLY_RANDOM_RATE",
    )
    auto_reply_cooldown: int = Field(
        default=30,
        ge=0,
        validation_alias="BOT_AUTO_REPLY_COOLDOWN",
    )

    # --- Bash 工具（skill 脚本执行；Windows Git Bash / WSL/Linux bash） ---
    bash_enabled: Flag = Field(
        default=True,
        validation_alias="BOT_BASH_ENABLED",
    )
    bash_shell: str = Field(
        default="bash",
        validation_alias="BOT_BASH_SHELL",
    )
    bash_timeout: int = Field(
        default=30,
        gt=0,
        validation_alias="BOT_BASH_TIMEOUT",
    )
    bash_max_output: int = Field(
        default=4000,
        ge=0,
        validation_alias="BOT_BASH_MAX_OUTPUT",
    )
    bash_allowed_roots: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias="BOT_BASH_ALLOWED_ROOTS",
    )

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> list[str]:
        return _parse_comma_list(value)

    @field_validator("bash_allowed_roots", mode="before")
    @classmethod
    def _parse_bash_allowed_roots(cls, value: object) -> list[str]:
        return _parse_comma_list(value)

    @model_validator(mode="after")
    def _validate_summary_ratios(self) -> "BotConfig":
        if self.summary_keep_ratio > self.summary_trigger_ratio:
            raise ValueError("summary_keep_ratio must be <= summary_trigger_ratio")
        if "embed_base_url" not in self.model_fields_set and self.llm_base_url:
            self.embed_base_url = self.llm_base_url
        if "embed_api_key" not in self.model_fields_set:
            self.embed_api_key = self.llm_api_key
        # 视觉已切 OpenAI 兼容：base_url / api_key 未设时回落主 LLM（同供应商零配置）
        if "vision_base_url" not in self.model_fields_set and self.llm_base_url:
            self.vision_base_url = self.llm_base_url
        if "vision_api_key" not in self.model_fields_set:
            self.vision_api_key = self.llm_api_key
        return self
