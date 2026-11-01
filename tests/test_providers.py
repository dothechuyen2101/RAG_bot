from types import SimpleNamespace

import pytest

from ragbot.config import Settings
from ragbot.providers.base import QuotaExhaustedError, is_quota_exhausted, is_transient
from ragbot.providers.gemini import GeminiLLM
from ragbot.providers.openai_provider import parse_json


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
