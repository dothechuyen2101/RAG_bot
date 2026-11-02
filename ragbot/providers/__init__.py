from urllib.parse import urlparse

from ..config import Settings, with_fallbacks
from .base import LLM, Embedder, TextFn


class MissingKeyError(RuntimeError):
    pass


def _require(key: str, env_name: str) -> str:
    if not key:
        raise MissingKeyError(f"Chưa có {env_name} trong file .env")
    return key


def openai_base_url(url: str) -> str:
    """API chuẩn OpenAI nằm dưới /v1, nhưng người dùng hay chỉ điền tên miền."""
    url = url.strip().rstrip("/")
    return url if urlparse(url).path else f"{url}/v1"


def get_llm(s: Settings, model: str = "") -> LLM:
    """model: model chính cho việc này (CONDENSE_MODEL, OCR_MODEL); để trống thì dùng model chat."""
    if s.llm_provider == "wellflow":
        from .openai_provider import ChatCompletionsLLM

        return ChatCompletionsLLM(
            _require(s.wellflow_api_key, "WELLFLOW_API_KEY"),
            openai_base_url(_require(s.wellflow_base_url, "WELLFLOW_BASE_URL")),
            with_fallbacks(model or s.wellflow_chat_model, s.wellflow_fallback_models),
            "wellflow",
        )
    if s.llm_provider == "openai":
        from .openai_provider import OpenAILLM

        return OpenAILLM(_require(s.openai_api_key, "OPENAI_API_KEY"), model or s.openai_chat_model,
                         s.openai_reasoning_effort)
    from .gemini import GeminiLLM

    return GeminiLLM(_require(s.gemini_api_key, "GEMINI_API_KEY"),
                     with_fallbacks(model or s.gemini_chat_model, s.gemini_fallback_models), s.gemini_thinking_level)


def get_embedder(s: Settings) -> Embedder:
    if s.embed_provider == "wellflow":
        from .openai_provider import OpenAIEmbedder

        return OpenAIEmbedder(
            _require(s.wellflow_api_key, "WELLFLOW_API_KEY"),
            _require(s.wellflow_embed_model, "WELLFLOW_EMBED_MODEL"),
            base_url=openai_base_url(_require(s.wellflow_base_url, "WELLFLOW_BASE_URL")),
            provider="wellflow",
        )
    if s.embed_provider == "openai":
        from .openai_provider import OpenAIEmbedder

        return OpenAIEmbedder(_require(s.openai_api_key, "OPENAI_API_KEY"), s.openai_embed_model)
    from .gemini import GeminiEmbedder

    return GeminiEmbedder(_require(s.gemini_api_key, "GEMINI_API_KEY"), s.gemini_embed_model, s.gemini_embed_dim)


__all__ = ["LLM", "Embedder", "MissingKeyError", "TextFn", "get_llm", "get_embedder", "openai_base_url"]
