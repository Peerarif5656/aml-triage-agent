"""LLM client for Groq's OpenAI-compatible API, with retries and a provider-neutral response type.

The agent only depends on `LLMResponse`, so swapping Groq for another
OpenAI-compatible endpoint (or a mock in tests) needs no agent changes.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .config import DEFAULT_MODEL, GROQ_BASE_URL, LLM_TEMPERATURE


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
                 max_retries: int = 4, timeout: float = 60.0):
        if not api_key:
            raise LLMError("No Groq API key provided")
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover - depends on environment
            raise LLMError("The 'openai' package is required for LLM mode: pip install openai") from e
        self.model = model
        self.max_retries = max_retries
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice: str = "auto") -> LLMResponse:
        kwargs = {"model": self.model, "messages": messages, "temperature": LLM_TEMPERATURE}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                break
            except Exception as e:  # SDK raises typed errors; classify by status/name
                last_error = e
                status = getattr(e, "status_code", None)
                text = str(e)
                retryable = (status in (429, 500, 502, 503, 504)
                             or type(e).__name__ in {"APITimeoutError", "APIConnectionError", "RateLimitError"}
                             or (status == 400 and "tool_use_failed" in text))
                if not retryable or attempt == self.max_retries:
                    raise LLMError(f"{type(e).__name__}: {text[:300]}") from e
                time.sleep(self._retry_after(e, attempt))
        else:  # pragma: no cover
            raise LLMError(str(last_error))

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
