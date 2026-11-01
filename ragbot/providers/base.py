from typing import Literal, Protocol

import httpx
import openai
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

EmbedKind = Literal["document", "query"]

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...

    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...

    def ocr(self, png: bytes, instruction: str) -> str: ...


class Embedder(Protocol):
    # Ghi vào collection để phát hiện khi đổi model (vector khác model không so sánh được).
    model_id: str

    def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]: ...


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TransportError, openai.APIConnectionError)):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status in TRANSIENT_STATUS


# Gói miễn phí hay bị 429, nên chờ lâu dần rồi thử lại.
api_retry = retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(6),
    reraise=True,
)


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
