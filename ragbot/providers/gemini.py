import json
import math

from google import genai
from google.genai import types

from .base import EmbedKind, api_retry, batched


class GeminiLLM:
    def __init__(self, api_key: str, model: str, thinking_level: str):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.thinking_level = thinking_level
        self.name = f"gemini:{model}"

    def _config(self, system: str | None = None, **kwargs) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level),
            **kwargs,
        )

    @api_retry
    def complete(self, system: str, user: str) -> str:
        resp = self.client.models.generate_content(model=self.model, contents=user, config=self._config(system))
        return resp.text or ""

    @api_retry
    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        resp = self.client.models.generate_content(
            model=self.model,
            contents=user,
            config=self._config(system, response_mime_type="application/json", response_json_schema=schema),
        )
        return json.loads(resp.text)

    @api_retry
    def ocr(self, png: bytes, instruction: str) -> str:
        resp = self.client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=png, mime_type="image/png"), instruction],
            config=self._config(),
        )
        return resp.text or ""


class GeminiEmbedder:
    BATCH_SIZE = 50

    def __init__(self, api_key: str, model: str, dim: int):
        self.client = genai.Client(api_key=api_key)
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
