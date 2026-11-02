from types import SimpleNamespace

import httpx
import openai
import pytest

from ragbot.config import Settings
from ragbot.providers.base import QuotaExhaustedError, is_quota_exhausted, is_transient
from ragbot.providers.gemini import GeminiLLM
from ragbot.providers import get_llm, openai_base_url
from ragbot.providers.openai_provider import ChatCompletionsLLM, parse_json


class ApiError(Exception):
    def __init__(self, code, details=None):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.details = details or {}


DAILY = ApiError(429, {"error": {"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})


def _llm(behaviour: dict) -> tuple[GeminiLLM, list[str]]:
    """behaviour: model -> mã lỗi HTTP (int) hoặc chuỗi trả về."""
    llm = GeminiLLM("test-key", ["model-a", "model-b", "model-c"], "low")
    called = []

    def generate_content(model, contents, config):
        called.append(model)
        result = behaviour[model]
        if isinstance(result, Exception):
            raise result
        if isinstance(result, int):
            raise ApiError(result)
        return SimpleNamespace(text=result)

    llm.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return llm, called


def test_overloaded_model_falls_back_to_next():
    llm, called = _llm({"model-a": 503, "model-b": 429, "model-c": "xin chào"})
    assert llm.complete("sys", "hi") == "xin chào"
    assert called == ["model-a", "model-b", "model-c"]


def test_healthy_primary_model_used_alone():
    llm, called = _llm({"model-a": '{"ok": true}', "model-b": "x", "model-c": "x"})
    assert llm.complete_json("sys", "hi", {}) == {"ok": True}
    assert called == ["model-a"]


def test_non_transient_error_is_not_masked_by_fallback():
    llm, called = _llm({"model-a": 400, "model-b": "x", "model-c": "x"})
    with pytest.raises(ApiError):
        llm.complete("sys", "hi")
    assert called == ["model-a"]


def test_daily_quota_exhausted_falls_back_without_waiting():
    llm, called = _llm({"model-a": DAILY, "model-b": "ok", "model-c": "x"})
    assert llm.complete("sys", "hi") == "ok"
    assert called == ["model-a", "model-b"]


def test_all_models_out_of_daily_quota_gives_clear_error():
    llm, called = _llm({"model-a": DAILY, "model-b": DAILY, "model-c": DAILY})
    with pytest.raises(QuotaExhaustedError, match="hết hạn mức trong ngày cho model-a, model-b, model-c"):
        llm.complete("sys", "hi")
    assert called == ["model-a", "model-b", "model-c"]  # không chờ rồi thử lại


def test_quota_detection():
    assert is_quota_exhausted(DAILY) and not is_transient(DAILY)
    assert is_transient(ApiError(429)) and not is_quota_exhausted(ApiError(429))


def test_parse_json_strips_code_fence():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json(' {"a": 2} ') == {"a": 2}


def test_gemini_models_setting_dedupes_and_keeps_order():
    s = Settings(_env_file=None, gemini_chat_model="m1", gemini_fallback_models=" m2, m1 ,m3,")
    assert s.gemini_models == ["m1", "m2", "m3"]


def _chat_llm(behaviour: dict) -> tuple[ChatCompletionsLLM, list[str]]:
    """Gateway tương thích OpenAI; behaviour: model -> Exception hoặc chuỗi trả về."""
    llm = ChatCompletionsLLM("wf_test", "http://localhost/v1", ["opus", "sonnet", "haiku"], "wellflow")
    called = []

    def create(model, messages, timeout, **kwargs):
        called.append(model)
        result = behaviour[model]
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))])

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return llm, called


def test_gateway_timeout_falls_back_to_next_model():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "http://localhost/v1/chat/completions"))
    llm, called = _chat_llm({"opus": timeout, "sonnet": '{"ok": 1}', "haiku": "x"})
    assert llm.complete_json("sys", "hi", {}) == {"ok": 1}
    assert called == ["opus", "sonnet"]
    assert llm.name == "wellflow:opus"


def test_openai_base_url_adds_v1_only_when_missing():
    assert openai_base_url("https://api.wellflow.dev") == "https://api.wellflow.dev/v1"
    assert openai_base_url("https://api.wellflow.dev/") == "https://api.wellflow.dev/v1"
    assert openai_base_url("https://api.wellflow.dev/v1") == "https://api.wellflow.dev/v1"
    assert openai_base_url("https://gw.example.com/openai/v2/") == "https://gw.example.com/openai/v2"


def test_wellflow_settings_defaults():
    s = Settings(_env_file=None)
    assert s.llm_provider == "wellflow" and s.embed_provider == "gemini"
    assert s.wellflow_models == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5.5"]


def test_task_model_keeps_shared_fallbacks():
    s = Settings(_env_file=None, wellflow_api_key="wf_test", ocr_model="claude-haiku-5.5")
    ocr = get_llm(s, s.ocr_model)
    assert ocr.models == ["claude-haiku-5.5", "claude-sonnet-5-5"] and ocr.name == "wellflow:claude-haiku-5.5"
    assert get_llm(s).models == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5.5"]
    g = Settings(_env_file=None, llm_provider="gemini", gemini_api_key="k", gemini_chat_model="m1",
                 gemini_fallback_models="m2")
    assert get_llm(g, "fast").models == ["fast", "m2"]


def _stream_llm(behaviour: dict) -> tuple[ChatCompletionsLLM, list]:
    """behaviour: model -> các mẩu chữ stream ra; một Exception trong danh sách giả lập đứt giữa chừng."""
    llm = ChatCompletionsLLM("wf_test", "http://localhost/v1", ["opus", "sonnet"], "wellflow")
    calls = []

    def create(model, messages, timeout, stream=False, **kwargs):
        calls.append((model, stream))
        parts = behaviour[model]

        def chunks():
            for p in parts:
                if isinstance(p, Exception):
                    raise p
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=p))])

        return chunks()

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return llm, calls


def test_stream_restarts_when_falling_back_mid_answer():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "http://localhost/v1/chat/completions"))
    llm, calls = _stream_llm({"opus": ["Mức ", "phạt", timeout], "sonnet": ["Phạt ", "6-8 triệu"]})
    seen = []
    assert llm.complete("sys", "hi", on_text=seen.append) == "Phạt 6-8 triệu"
    # Chuỗi rỗng báo giao diện xóa phần của model trước.
    assert seen == ["", "Mức ", "Mức phạt", "", "Phạt ", "Phạt 6-8 triệu"]
    assert calls == [("opus", True), ("sonnet", True)]


def test_gateway_without_streaming_falls_back_to_plain_call():
    req = httpx.Request("POST", "http://localhost/v1/chat/completions")
    rejected = openai.BadRequestError("stream not supported", response=httpx.Response(400, request=req), body=None)
    llm = ChatCompletionsLLM("wf_test", "http://localhost/v1", ["opus"], "wellflow")
    calls = []

    def create(model, messages, timeout, stream=False, **kwargs):
        calls.append(stream)
        if stream:
            raise rejected
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": 1}'))])

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    seen = []
    assert llm.complete_json("sys", "hi", {}, on_text=seen.append) == {"ok": 1}
    assert calls == [True, False] and seen == ["", ""]


def test_gemini_streams_text():
    llm, called = _llm({})

    def generate_content_stream(model, contents, config):
        called.append(model)
        yield SimpleNamespace(text='{"answer": "Ph')
        yield SimpleNamespace(text='ạt"}')

    llm.client.models.generate_content_stream = generate_content_stream
    seen = []
    assert llm.complete_json("sys", "hi", {}, on_text=seen.append) == {"answer": "Phạt"}
    assert seen == ["", '{"answer": "Ph', '{"answer": "Phạt"}'] and called == ["model-a"]
