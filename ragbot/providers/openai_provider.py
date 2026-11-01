import base64
import json

import openai

from .base import CHAT_TIMEOUT_S, OCR_TIMEOUT_S, REQUEST_TIMEOUT_S, EmbedKind, api_retry, batched, call_with_fallback


class OpenAILLM:
    def __init__(self, api_key: str, model: str, reasoning_effort: str):
        self.client = openai.OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=0)
        self.model = model
        self.reasoning = {"effort": reasoning_effort}
        self.name = f"openai:{model}"

    @api_retry
    def complete(self, system: str, user: str) -> str:
        resp = self.client.responses.create(
            model=self.model, instructions=system, input=user, reasoning=self.reasoning, store=False,
            timeout=CHAT_TIMEOUT_S,
        )
        return resp.output_text

    @api_retry
    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        resp = self.client.responses.create(
            model=self.model,
            instructions=system,
            input=user,
            reasoning=self.reasoning,
            text={"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}},
            store=False,
            timeout=CHAT_TIMEOUT_S,
        )
        return json.loads(resp.output_text)

    @api_retry
    def ocr(self, png: bytes, instruction: str) -> str:
        image_url = "data:image/png;base64," + base64.b64encode(png).decode()
        resp = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instruction},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
            reasoning=self.reasoning,
            store=False,
            timeout=OCR_TIMEOUT_S,
        )
        return resp.output_text


class ChatCompletionsLLM:
    """Dịch vụ tương thích OpenAI qua /chat/completions (Wellflow và các gateway tương tự)."""

    def __init__(self, api_key: str, base_url: str, models: list[str], provider: str):
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=REQUEST_TIMEOUT_S, max_retries=0)
        # Model đầu là model chính, các model sau dùng khi model trước treo hoặc quá tải.
        self.models = models
        self.provider = provider
        self.name = f"{provider}:{models[0]}"

    @api_retry
    def _chat(self, messages: list[dict], timeout: int, **kwargs) -> str:
        def call(model: str) -> str:
            resp = self.client.chat.completions.create(model=model, messages=messages, timeout=timeout, **kwargs)
            return resp.choices[0].message.content or ""

        return call_with_fallback(self.models, call, f"{self.provider} đã hết hạn mức cho {{models}}.")

    def complete(self, system: str, user: str) -> str:
        return self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}], CHAT_TIMEOUT_S)

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        text = self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            CHAT_TIMEOUT_S,
            response_format={"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}},
        )
        return parse_json(text)

    def ocr(self, png: bytes, instruction: str) -> str:
        image_url = "data:image/png;base64," + base64.b64encode(png).decode()
        content = [{"type": "text", "text": instruction}, {"type": "image_url", "image_url": {"url": image_url}}]
        return self._chat([{"role": "user", "content": content}], OCR_TIMEOUT_S)


def parse_json(text: str) -> dict:
    """Một số gateway bọc JSON trong ```json ... ``` dù đã yêu cầu JSON thuần."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


class OpenAIEmbedder:
    BATCH_SIZE = 100

    def __init__(self, api_key: str, model: str, base_url: str | None = None, provider: str = "openai"):
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=REQUEST_TIMEOUT_S, max_retries=0)
        self.model = model
        self.model_id = f"{provider}:{model}"

    def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in batched(texts, self.BATCH_SIZE):
            out.extend(self._embed_batch(batch))
        return out

    @api_retry
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        # encoding_format="float": SDK mặc định xin base64, một số gateway không hỗ trợ.
        resp = self.client.embeddings.create(model=self.model, input=texts, encoding_format="float")
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
