import json
import math

from google import genai
from google.genai import types

from .base import (
    CHAT_TIMEOUT_S,
    OCR_TIMEOUT_S,
    REQUEST_TIMEOUT_S,
    EmbedKind,
    TextFn,
    api_retry,
    batched,
    call_with_fallback,
)

QUOTA_MESSAGE = (
    "Gemini đã hết hạn mức trong ngày cho {models} (gói miễn phí chỉ có khoảng 20 lượt/ngày mỗi model). "
    "Hạn mức đặt lại lúc 0h giờ Pacific, khoảng 15h giờ Việt Nam. Muốn dùng nhiều hơn thì bật thanh toán "
    "trong Google AI Studio, hoặc đổi LLM_PROVIDER trong file .env."
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
    def _generate(self, contents, config: types.GenerateContentConfig, on_text: TextFn | None = None) -> str:
        def call(model: str) -> str:
            if on_text is None:
                return self.client.models.generate_content(model=model, contents=contents, config=config).text or ""
            on_text("")
            text = ""
            for chunk in self.client.models.generate_content_stream(model=model, contents=contents, config=config):
                if chunk.text:
                    text += chunk.text
                    on_text(text)
            return text

        return call_with_fallback(self.models, call, QUOTA_MESSAGE)

    def complete(self, system: str, user: str, on_text: TextFn | None = None) -> str:
        return self._generate(user, self._config(system), on_text)

    def complete_json(self, system: str, user: str, schema: dict, on_text: TextFn | None = None) -> dict:
        config = self._config(system, response_mime_type="application/json", response_json_schema=schema)
        return json.loads(self._generate(user, config, on_text))

    def ocr(self, png: bytes, instruction: str) -> str:
        contents = [types.Part.from_bytes(data=png, mime_type="image/png"), instruction]
        return self._generate(contents, self._config(timeout_s=OCR_TIMEOUT_S))


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
