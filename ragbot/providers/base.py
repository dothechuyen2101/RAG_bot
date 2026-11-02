import logging
from collections.abc import Callable
from typing import Literal, Protocol, TypeVar

import httpx
import openai
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

EmbedKind = Literal["document", "query"]
# Nhận TOÀN BỘ chữ đã sinh tới lúc đó (không phải phần mới). Khi chuyển sang model dự phòng
# hoặc thử lại, được gọi với chuỗi rỗng để giao diện xóa phần đã hiện.
TextFn = Callable[[str], None]

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
# Model khỏe trả lời chat trong vài giây, OCR một trang trong khoảng 10 giây.
# Chờ lâu hơn các mức này nghĩa là model đang quá tải, nên bỏ để thử model/lần khác.
CHAT_TIMEOUT_S = 30
OCR_TIMEOUT_S = 90
REQUEST_TIMEOUT_S = 60  # mặc định cho các request khác (embedding)

log = logging.getLogger("ragbot.providers")


class LLM(Protocol):
    name: str

    # on_text: nhận chữ dần dần trong lúc model đang sinh (stream).
    def complete(self, system: str, user: str, on_text: TextFn | None = None) -> str: ...

    def complete_json(self, system: str, user: str, schema: dict, on_text: TextFn | None = None) -> dict: ...

    def ocr(self, png: bytes, instruction: str) -> str: ...


class Embedder(Protocol):
    # Ghi vào collection để phát hiện khi đổi model (vector khác model không so sánh được).
    model_id: str

    def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]: ...


class QuotaExhaustedError(RuntimeError):
    pass


def is_quota_exhausted(exc: BaseException) -> bool:
    """429 do hết hạn mức THEO NGÀY (gói miễn phí): thử lại vài phút cũng vô ích."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status == 429 and "perday" in str(getattr(exc, "details", exc)).replace(" ", "").lower()


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TransportError, openai.APIConnectionError)):
        return True
    if is_quota_exhausted(exc):
        return False
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status in TRANSIENT_STATUS


def describe_error(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return f"{status} {type(exc).__name__}" if status else type(exc).__name__


T = TypeVar("T")


def call_with_fallback(models: list[str], call: Callable[[str], T], quota_message: str) -> T:
    """Gọi lần lượt từng model. Model quá tải hoặc treo (503/429/504/timeout) thì chuyển ngay sang model
    kế tiếp; model hết hạn mức theo ngày thì bỏ qua. Lỗi tạm thời ở model cuối được ném ra để api_retry
    chờ một lúc rồi thử lại từ đầu danh sách."""
    exhausted = []
    for i, model in enumerate(models):
        is_last = i == len(models) - 1
        try:
            return call(model)
        except Exception as e:
            if is_quota_exhausted(e):
                exhausted.append(model)
                if is_last:
                    break
                log.warning("%s đã hết hạn mức trong ngày, chuyển sang %s", model, models[i + 1])
                continue
            if not is_transient(e) or is_last:
                raise
            log.warning("%s lỗi tạm thời (%s), chuyển sang %s", model, describe_error(e), models[i + 1])
    raise QuotaExhaustedError(quota_message.format(models=", ".join(exhausted)))


# Gói miễn phí hay bị 429, nên chờ lâu dần rồi thử lại.
api_retry = retry(
    retry=retry_if_exception(is_transient),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(6) | stop_after_delay(240),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
