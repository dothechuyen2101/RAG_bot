import base64
import json

import openai

from .base import (
    CHAT_TIMEOUT_S,
    OCR_TIMEOUT_S,
    REQUEST_TIMEOUT_S,
    EmbedKind,
    TextFn,
    api_retry,
    batched,
    call_with_fallback,
    describe_error,
    log,
)

# Gateway không hỗ trợ stream (hoặc stream kèm JSON schema) thường trả các mã này: gọi lại không stream.
NO_STREAM_STATUS = {400, 422, 501}


class OpenAILLM:
    def __init__(self, api_key: str, model: str, reasoning_effort: str):
        self.client = openai.OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=0)
        self.model = model
        self.reasoning = {"effort": reasoning_effort}
        self.name = f"openai:{model}"

    @api_retry
    def _respond(self, on_text: TextFn | None, **kwargs) -> str:
        kwargs = dict(model=self.model, reasoning=self.reasoning, store=False, timeout=CHAT_TIMEOUT_S, **kwargs)
        if on_text is None:
            return self.client.responses.create(**kwargs).output_text
        on_text("")
        text = ""
        for event in self.client.responses.create(**kwargs, stream=True):
            if event.type == "response.output_text.delta":
                text += event.delta
                on_text(text)
        return text

    def complete(self, system: str, user: str, on_text: TextFn | None = None) -> str:
        return self._respond(on_text, instructions=system, input=user)

    def complete_json(self, system: str, user: str, schema: dict, on_text: TextFn | None = None) -> dict:
        return json.loads(self._respond(
            on_text, instructions=system, input=user,
            text={"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}},
        ))

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

    def _chat(self, messages: list[dict], timeout: int, on_text: TextFn | None = None, **kwargs) -> str:
        if on_text is None:
            return self._chat_models(messages, timeout, None, **kwargs)
        try:
            return self._chat_models(messages, timeout, on_text, **kwargs)
        except openai.APIStatusError as e:
            if e.status_code not in NO_STREAM_STATUS:
                raise
            log.warning("%s không stream được (%s), gọi lại không stream", self.provider, describe_error(e))
            on_text("")
            return self._chat_models(messages, timeout, None, **kwargs)

    @api_retry
    def _chat_models(self, messages: list[dict], timeout: int, on_text: TextFn | None, **kwargs) -> str:
        def call(model: str) -> str:
            if on_text is None:
                resp = self.client.chat.completions.create(model=model, messages=messages, timeout=timeout, **kwargs)
                return resp.choices[0].message.content or ""
            on_text("")
            text = ""
            # timeout áp cho từng lần chờ dữ liệu, nên câu trả lời dài vẫn chạy hết miễn là model không ngừng giữa chừng.
            for chunk in self.client.chat.completions.create(model=model, messages=messages, timeout=timeout,
                                                             stream=True, **kwargs):
                if chunk.choices and (delta := chunk.choices[0].delta.content):
                    text += delta
                    on_text(text)
            return text

        return call_with_fallback(self.models, call, f"{self.provider} đã hết hạn mức cho {{models}}.")

    def complete(self, system: str, user: str, on_text: TextFn | None = None) -> str:
        return self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}], CHAT_TIMEOUT_S,
                          on_text)

    def complete_json(self, system: str, user: str, schema: dict, on_text: TextFn | None = None) -> dict:
        text = self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            CHAT_TIMEOUT_S,
            on_text,
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
