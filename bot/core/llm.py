import logging

from langchain_openai import ChatOpenAI

from common import BotConfig

logger = logging.getLogger(__name__)

_llm: ChatOpenAI | None = None


def setup_llm(config: BotConfig) -> ChatOpenAI:
    """Create and cache a ChatOpenAI instance from :class:`BotConfig`."""
    global _llm
    if _llm is not None:
        return _llm

    if not config.llm_base_url:
        raise RuntimeError("BASE_URL not set in .env")
    if not config.llm_api_key:
        raise RuntimeError("API_KEY not set in .env")

    _llm = ChatOpenAI(
        model=config.llm_model,
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        temperature=config.llm_temperature,
        max_retries=config.llm_max_retries,
        request_timeout=config.llm_request_timeout,
    )
    logger.info(
        "LLM configured: model=%s temperature=%.2f base_url=%s",
        config.llm_model,
        config.llm_temperature,
        config.llm_base_url,
    )
    return _llm
