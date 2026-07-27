import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

_llm: ChatOpenAI | None = None


def setup_llm(
    *,
    model: str = "deepseek-v4-flash",
    temperature: float = 0.7,
    max_retries: int = 1,
    request_timeout: int = 30,
) -> ChatOpenAI:
    """Create and cache a ChatOpenAI instance configured for OpenCode AI.

    Reads ``GO_BASE_URL`` and ``GO_API_KEY`` from ``.env``.

    Parameters
    ----------
    model, temperature, max_retries, request_timeout :
        LLM settings; passed through from :class:`BotConfig`.
    """
    global _llm
    if _llm is not None:
        return _llm

    load_dotenv()
    base_url = os.getenv("GO_BASE_URL")
    api_key = os.getenv("GO_API_KEY")

    if not base_url:
        raise RuntimeError("GO_BASE_URL not set in .env")
    if not api_key:
        raise RuntimeError("GO_API_KEY not set in .env")

    _llm = ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_retries=max_retries,
        request_timeout=request_timeout,
    )
    logger.info(
        "LLM configured: model=%s temperature=%.2f base_url=%s",
        model,
        temperature,
        base_url,
    )
    return _llm
