from ..config import Settings
from .base import LLM, Embedder


class MissingKeyError(RuntimeError):
    pass


def _require(key: str, env_name: str) -> str:
    if not key:
        raise MissingKeyError(f"Chưa có {env_name} trong file .env")
    return key


def get_llm(s: Settings) -> LLM:
    if s.llm_provider == "openai":
        from .openai_provider import OpenAILLM

        return OpenAILLM(_require(s.openai_api_key, "OPENAI_API_KEY"), s.openai_chat_model, s.openai_reasoning_effort)
    from .gemini import GeminiLLM

    return GeminiLLM(_require(s.gemini_api_key, "GEMINI_API_KEY"), s.gemini_chat_model, s.gemini_thinking_level)


def get_embedder(s: Settings) -> Embedder:
    if s.embed_provider == "openai":
        from .openai_provider import OpenAIEmbedder

        return OpenAIEmbedder(_require(s.openai_api_key, "OPENAI_API_KEY"), s.openai_embed_model)
    from .gemini import GeminiEmbedder

    return GeminiEmbedder(_require(s.gemini_api_key, "GEMINI_API_KEY"), s.gemini_embed_model, s.gemini_embed_dim)


__all__ = ["LLM", "Embedder", "MissingKeyError", "get_llm", "get_embedder"]
