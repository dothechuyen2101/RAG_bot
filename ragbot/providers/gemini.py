import json
import math

from google import genai
from google.genai import types

from .base import (
    CHAT_TIMEOUT_S,
    OCR_TIMEOUT_S,
    REQUEST_TIMEOUT_S,
    EmbedKind,
    api_retry,
    batched,
    QuotaExhaustedError,
    describe_error,
    is_quota_exhausted,
    is_transient,
    log,
)


def _client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_S * 1000))


class GeminiLLM:
    def __init__(self, api_key: str, models: list[str], thinking_level: str):
        self.client = _client(api_key)
        # Model đầu là model chính, các model sau dùng khi model trước đang quá tải.
        self.models = models
        self.thinking_level = thinking_level
        self.name = f"gemini:{models[0]}"

    def _config(self, system: str | None = None, timeout_s: int = CHAT_TIMEOUT_S, **kwargs) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level),
            http_options=types.HttpOptions(timeout=timeout_s * 1000),
            **kwargs,
        )

    @api_retry
    def _generate(self, contents, config: types.GenerateContentConfig) -> types.GenerateContentResponse:
        """Gọi lần lượt từng model. Model báo quá tải (503/429/504) thì chuyển ngay sang model kế tiếp;
        hết danh sách thì api_retry chờ một lúc rồi thử lại từ đầu."""
        exhausted = []
        for i, model in enumerate(self.models):
            is_last = i == len(self.models) - 1
            try:
                return self.client.models.generate_content(model=model, contents=contents, config=config)
            except Exception as e:
                if is_quota_exhausted(e):
                    exhausted.append(model)
                    if is_last:
                        break
                    log.warning("%s đã hết hạn mức trong ngày, chuyển sang %s", model, self.models[i + 1])
                    continue
                if not is_transient(e) or is_last:
                    raise
                log.warning("%s lỗi tạm thời (%s), chuyển sang %s", model, describe_error(e), self.models[i + 1])
        raise QuotaExhaustedError(
            f"Gemini đã hết hạn mức trong ngày cho {', '.join(exhausted)} (gói miễn phí chỉ có khoảng 20 lượt/ngày "
            "mỗi model). Hạn mức đặt lại lúc 0h giờ Pacific, khoảng 15h giờ Việt Nam. Muốn dùng nhiều hơn thì "
            "bật thanh toán trong Google AI Studio, hoặc đổi LLM_PROVIDER trong file .env."
        )

    def complete(self, system: str, user: str) -> str:
        return self._generate(user, self._config(system)).text or ""

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        config = self._config(system, response_mime_type="application/json", response_json_schema=schema)
        return json.loads(self._generate(user, config).text)

    def ocr(self, png: bytes, instruction: str) -> str:
        contents = [types.Part.from_bytes(data=png, mime_type="image/png"), instruction]
        return self._generate(contents, self._config(timeout_s=OCR_TIMEOUT_S)).text or ""


class GeminiEmbedder:
    BATCH_SIZE = 50

    def __init__(self, api_key: str, model: str, dim: int):
        self.client = _client(api_key)
        self.model = model
        self.dim = dim
        self.model_id = f"gemini:{model}:{dim}"
        # gemini-embedding-2 không nhận task_type, thay vào đó dùng tiền tố trong nội dung.
        self.prefix_mode = "gemini-embedding-2" in model

    def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in batched(texts, self.BATCH_SIZE):
            out.extend(self._embed_batch(batch, kind))
        return out

    @api_retry
    def _embed_batch(self, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        if self.prefix_mode:
            if kind == "query":
                texts = [f"task: search result | query: {t}" for t in texts]
            else:
                texts = [f"title: none | text: {t}" for t in texts]
            config = types.EmbedContentConfig(output_dimensionality=self.dim)
        else:
            task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
            config = types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim)

        # Mỗi đoạn phải là một Content riêng: truyền list chuỗi thì gemini-embedding-2
        # gộp tất cả thành MỘT vector.
        contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in texts]
        resp = self.client.models.embed_content(model=self.model, contents=contents, config=config)
        vectors = [list(e.values) for e in resp.embeddings]
        if len(vectors) != len(texts):
            raise RuntimeError(f"Gemini trả về {len(vectors)} vector cho {len(texts)} đoạn văn bản")
        if not self.prefix_mode:
            # gemini-embedding-001 không tự chuẩn hóa vector khi giảm số chiều.
            vectors = [_normalize(v) for v in vectors]
        return vectors


def _normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]
