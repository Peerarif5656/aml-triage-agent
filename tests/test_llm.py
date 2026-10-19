"""GroqClient behaviour without network: model fallback, retries and response parsing."""
from types import SimpleNamespace as NS

import pytest

from aml_agent.llm import GroqClient, LLMError, extract_json, usable_models


class APIError(Exception):
    def __init__(self, status, text, retry_after=None):
        super().__init__(text)
        self.status_code = status
        self.response = NS(headers={"retry-after": retry_after} if retry_after else {})


class FakeCompletions:
    def __init__(self, behaviour):
        self.behaviour = behaviour  # model -> list of outcomes ("ok" or an exception)
        self.models_called = []

    def create(self, **kw):
        self.models_called.append(kw["model"])
        outcome = self.behaviour[kw["model"]].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        msg = NS(content='{"disposition": "CLOSE"}', tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=10, completion_tokens=5))


def make_client(model, fallbacks, behaviour, max_retries=2):
    c = GroqClient.__new__(GroqClient)  # skip __init__: no SDK or network needed
    c.model, c.max_retries, c.fallback_models, c.model_switches = model, max_retries, list(fallbacks), []
    c._client = NS(chat=NS(completions=FakeCompletions(behaviour)))
    return c


def gone(model):
    return APIError(404, f"Error code: 404 - The model `{model}` does not exist ... 'code': 'model_not_found'")


def test_retired_model_switches_to_next_available():
    c = make_client("llama-3.3-70b-versatile", ["openai/gpt-oss-120b"],
                    {"llama-3.3-70b-versatile": [gone("llama-3.3-70b-versatile")], "openai/gpt-oss-120b": ["ok"]})
    resp = c.chat([{"role": "user", "content": "hi"}])
    assert c.model == "openai/gpt-oss-120b"
    assert c.model_switches == [{"from": "llama-3.3-70b-versatile", "to": "openai/gpt-oss-120b",
                                 "reason": "model_not_found"}]
    assert extract_json(resp.content) == {"disposition": "CLOSE"}


def test_model_not_found_without_fallbacks_raises():
    c = make_client("gone-model", [], {"gone-model": [gone("gone-model")]})
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "hi"}])


def test_rate_limit_is_retried_then_succeeds():
    c = make_client("m", [], {"m": [APIError(429, "rate limit", retry_after="0"), "ok"]})
    resp = c.chat([{"role": "user", "content": "hi"}])
    assert resp.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert c._client.chat.completions.models_called == ["m", "m"]


def test_auth_error_is_not_retried():
    c = make_client("m", ["other"], {"m": [APIError(401, "invalid api key")], "other": ["ok"]})
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "hi"}])
    assert c._client.chat.completions.models_called == ["m"]


def test_usable_models_keeps_preference_order():
    assert usable_models(["qwen/qwen3.8-27b", "openai/gpt-oss-120b"],
                         ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]) == \
        ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert usable_models(None, ["a", "b"]) == ["a", "b"]
    assert usable_models(["x"], ["a", "b"]) == ["a", "b"]
