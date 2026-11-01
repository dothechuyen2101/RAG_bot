from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["wellflow", "gemini", "openai"] = "wellflow"
    # Wellflow chưa có API embedding (/v1/embeddings trả 501), nên embedding mặc định dùng Gemini.
    embed_provider: Literal["gemini", "wellflow", "openai"] = "gemini"

    # Wellflow: gateway tương thích OpenAI (/v1/chat/completions).
    wellflow_api_key: str = ""
    wellflow_base_url: str = "https://api.wellflow.dev/v1"
    wellflow_chat_model: str = "claude-opus-5-5"
    wellflow_fallback_models: str = "claude-sonnet-5-5,claude-haiku-5.5"
    wellflow_embed_model: str = ""

    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-3.6-flash"
    # Model dự phòng khi model chính quá tải, cách nhau bởi dấu phẩy.
    gemini_fallback_models: str = "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.8-flash"
    gemini_thinking_level: Literal["low", "medium", "high"] = "low"
    gemini_embed_model: str = "gemini-embedding-2"
    gemini_embed_dim: int = 1536

    openai_api_key: str = ""
    openai_chat_model: str = "gpt-6.1-terra"
    openai_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    openai_embed_model: str = "text-embedding-3-small"

    tavily_api_key: str = ""
    tavily_max_results: int = 5

    top_k: int = 8
    min_similarity: float = 0.0
    ocr_workers: int = 4

    data_dir: Path = ROOT / "data"

    @property
    def gemini_models(self) -> list[str]:
        return _with_fallbacks(self.gemini_chat_model, self.gemini_fallback_models)

    @property
    def wellflow_models(self) -> list[str]:
        return _with_fallbacks(self.wellflow_chat_model, self.wellflow_fallback_models)


def _with_fallbacks(primary: str, fallbacks: str) -> list[str]:
    """Model chính rồi tới các model dự phòng (chuỗi cách nhau bởi dấu phẩy), bỏ trùng."""
    return list(dict.fromkeys([primary, *(m.strip() for m in fallbacks.split(",") if m.strip())]))


@lru_cache
def get_settings() -> Settings:
    return Settings()
