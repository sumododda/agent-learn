"""OpenRouter API wrapper using LangChain's ChatOpenAI."""
import logging

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"


def build_chat_model(provider: str, model: str, credentials: dict, extra_fields: dict | None = None) -> ChatOpenAI:
    """Create a LangChain ChatOpenAI pointed at OpenRouter."""
    api_key = credentials.get("api_key", "")
    key_hint = f"****{api_key[-4:]}" if len(api_key) >= 4 else "****"
    logger.debug("[llm] Building ChatOpenAI (model=%s, key=%s)", model, key_hint)
    return ChatOpenAI(
        base_url=OPENROUTER_BASE,
        api_key=api_key,
        model=model,
        request_timeout=120,
    )


async def validate_credentials(api_key: str) -> bool:
    """Test an OpenRouter API key with a lightweight call."""
    try:
        llm = ChatOpenAI(
            base_url=OPENROUTER_BASE,
            api_key=api_key,
            model=DEFAULT_MODEL,
            max_tokens=5,
            request_timeout=30,
        )
        await llm.ainvoke("Hi")
        return True
    except Exception as e:
        logger.warning("Credential validation failed: %s", e)
        return False
