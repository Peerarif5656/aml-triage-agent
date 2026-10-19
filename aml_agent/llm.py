"""LLM client for Groq's OpenAI-compatible API, with retries and a provider-neutral response type.

The agent only depends on `LLMResponse`, so swapping Groq for another
OpenAI-compatible endpoint (or a mock in tests) needs no agent changes.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .config import AVAILABLE_MODELS, DEFAULT_MODEL, GROQ_BASE_URL, LLM_TEMPERATURE, MAX_COMPLETION_TOKENS


class LLMError(RuntimeError):
    """Raised when the LLM cannot be reached or keeps failing."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, exactly as the model produced it


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def as_message(self) -> dict:
        msg = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.name, "arguments": tc.arguments}}
                                 for tc in self.tool_calls]
        return msg


class GroqClient:
    """Thin wrapper over the OpenAI SDK pointed at Groq."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str = GROQ_BASE_URL,
                 max_retries: int = 4, timeout: float = 60.0, fallback_models: list[str] | None = None):
        if not api_key:
            raise LLMError("No Groq API key provided")
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover - depends on environment
            raise LLMError("The 'openai' package is required for LLM mode: pip install openai") from e
        self.model = model
        self.max_retries = max_retries
        # Providers retire models; if the chosen one is gone, try these in order instead of failing.
        self.fallback_models = [m for m in (fallback_models if fallback_models is not None else AVAILABLE_MODELS)
                                if m != model]
        self.model_switches: list[dict] = []
        self.on_retry = None  # optional callback(wait_seconds, reason) so callers can show waits
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)

    @staticmethod
    def _model_options(model: str) -> dict:
        # gpt-oss reasons before answering; low effort is plenty for a tool-driven investigation and uses
        # far fewer tokens per turn (which is what the free tier's per-minute limit counts).
        body = {"max_completion_tokens": MAX_COMPLETION_TOKENS}
        if model.startswith("openai/gpt-oss"):
            body.update({"reasoning_effort": "low", "include_reasoning": False})
        return {"extra_body": body}

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice: str = "auto") -> LLMResponse:
        kwargs = {"messages": messages, "temperature": LLM_TEMPERATURE}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        attempt = 0
        while True:
            kwargs["model"] = self.model
            kwargs.update(self._model_options(self.model))
            try:
                resp = self._client.chat.completions.create(**kwargs)
                break
            except Exception as e:  # SDK raises typed errors; classify by status/name
                status = getattr(e, "status_code", None)
                text = str(e)
                if status == 404 and "model_not_found" in text and self.fallback_models:
                    retired, self.model = self.model, self.fallback_models.pop(0)
                    self.model_switches.append({"from": retired, "to": self.model, "reason": "model_not_found"})
                    continue
                retryable = (status in (429, 500, 502, 503, 504)
                             or type(e).__name__ in {"APITimeoutError", "APIConnectionError", "RateLimitError"}
                             or (status == 400 and "tool_use_failed" in text))
                if not retryable or attempt >= self.max_retries:
                    raise LLMError(f"{type(e).__name__}: {text[:300]}") from e
                wait = self._retry_after(e, attempt)
                if self.on_retry:
                    reason = ("free-tier rate limit" if status == 429 or type(e).__name__ == "RateLimitError"
                              else "temporary provider error")
                    try:
                        self.on_retry(wait, reason)
                    except Exception:
                        pass
                time.sleep(wait)
                attempt += 1

        msg = resp.choices[0].message
        calls = [ToolCall(tc.id, tc.function.name, tc.function.arguments or "{}") for tc in (msg.tool_calls or [])]
        usage = {}
        if getattr(resp, "usage", None):
            usage = {"prompt_tokens": resp.usage.prompt_tokens or 0,
                     "completion_tokens": resp.usage.completion_tokens or 0}
        return LLMResponse(content=msg.content, tool_calls=calls, usage=usage)

    @staticmethod
    def _retry_after(error, attempt: int) -> float:
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
        try:
            return min(float(headers.get("retry-after", "")), 30.0)
        except (TypeError, ValueError):
            return min(2 ** (attempt + 1), 20)


def list_groq_models(api_key: str, base_url: str = GROQ_BASE_URL) -> list[str] | None:
    """Model IDs this API key can use right now, or None if the list cannot be fetched."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=15.0, max_retries=1)
        return sorted(m.id for m in client.models.list())
    except Exception:
        return None


def usable_models(available: list[str] | None, preferred: list[str] = AVAILABLE_MODELS) -> list[str]:
    """Preferred models the account can use, in preference order; the full preferred list if unknown."""
    if not available:
        return list(preferred)
    return [m for m in preferred if m in available] or list(preferred)


def extract_json(text: str | None) -> dict | None:
    """Parse a JSON object from a model reply, tolerating code fences or stray text."""
    if not text:
        return None
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None
