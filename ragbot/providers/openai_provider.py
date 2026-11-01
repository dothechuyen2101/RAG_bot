import base64
import json

import openai

from .base import EmbedKind, api_retry, batched


class OpenAILLM:
    def __init__(self, api_key: str, model: str, reasoning_effort: str):
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model
        self.reasoning = {"effort": reasoning_effort}
        self.name = f"openai:{model}"

    @api_retry
    def complete(self, system: str, user: str) -> str:
        resp = self.client.responses.create(
            model=self.model, instructions=system, input=user, reasoning=self.reasoning, store=False
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
        )
        return resp.output_text


class OpenAIEmbedder:
    BATCH_SIZE = 100

    def __init__(self, api_key: str, model: str):
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model
        self.model_id = f"openai:{model}"

    def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in batched(texts, self.BATCH_SIZE):
            out.extend(self._embed_batch(batch))
        return out

    @api_retry
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
