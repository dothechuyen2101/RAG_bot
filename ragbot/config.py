from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["gemini", "openai"] = "gemini"
    embed_provider: Literal["gemini", "openai"] = "gemini"

    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-3.8-flash"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
