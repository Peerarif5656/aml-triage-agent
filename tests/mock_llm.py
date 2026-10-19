"""A scripted stand-in for the LLM so the agent loop can be tested without network or API keys."""
import json

from aml_agent.llm import LLMError, LLMResponse, ToolCall


class ScriptedLLM:
    model = "scripted-mock"

    def __init__(self, script):
        """script: list of steps. A step is either a list of (tool_name, args) tuples
        (the model calls tools) or a str/dict (the model's final reply)."""
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls.append({"messages": [dict(m) for m in messages], "tool_choice": tool_choice,
                           "tools": [t["function"]["name"] for t in (tools or [])]})
        if not self.script:
            raise LLMError("script exhausted")
        step = self.script.pop(0)
        if isinstance(step, list):
            calls = [ToolCall(id=f"call_{len(self.calls)}_{i}", name=n, arguments=json.dumps(a))
                     for i, (n, a) in enumerate(step)]
            return LLMResponse(content=None, tool_calls=calls, usage={"prompt_tokens": 100, "completion_tokens": 20})
        content = step if isinstance(step, str) else json.dumps(step)
        return LLMResponse(content=content, usage={"prompt_tokens": 100, "completion_tokens": 50})


class FailingLLM:
    model = "unreachable"

    def chat(self, *a, **k):
        raise LLMError("RateLimitError: simulated outage")
