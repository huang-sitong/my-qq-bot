import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

_llm: ChatOpenAI | None = None


def setup_llm() -> ChatOpenAI:
    """Create and cache a ChatOpenAI instance configured for OpenCode AI.

    Reads ``GO_BASE_URL`` and ``GO_API_KEY`` from ``.env``.
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
        model="deepseek-v4-flash",
        base_url=base_url,
        api_key=api_key,
        temperature=0.7,
        max_retries=1,
        request_timeout=30,
    )
    logger.info("LLM configured: model=deepseek-v4-flash, base_url=%s", base_url)
    return _llm
