"""Translate Anthropic messages <-> OpenAI chat completions.

The agent and the cassettes speak the Anthropic messages shape. Providers that
only offer an OpenAI-compatible endpoint (Groq, OpenAI, Together, vLLM, Ollama)
are reached by translating at the proxy, so the agent, the cassette format and
the isolation model do not change, and the credential still lives only in the
proxy container.

Pure functions: no I/O, no HTTP. Unit tested without a daemon.
"""

from __future__ import annotations

import json
from typing import Any

# OpenAI finish_reason -> Anthropic stop_reason.
_STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def _flatten_text(content: Any) -> str:
    """Anthropic allows a string or a list of text blocks for system/user text."""
    if isinstance(content, str):
        return content
    parts = [str(b.get("text", "")) for b in content or [] if b.get("type") == "text"]
    return "\n".join(parts)


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _flatten_text(content)
    return "" if content is None else json.dumps(content, sort_keys=True)


def _translate_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message["role"]
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    blocks = content or []
    if role == "assistant":
        text = _flatten_text(blocks)
        tool_calls = [
            {
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input", {}), sort_keys=True),
                },
            }
            for block in blocks
            if block.get("type") == "tool_use"
        ]
        out: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            out["tool_calls"] = tool_calls
        return [out]

    # user turn: tool_result blocks become their own `tool` messages, which OpenAI
    # requires to directly follow the assistant tool_calls message.
    translated: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") == "tool_result":
            translated.append(
                {
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": _tool_result_text(block.get("content")),
                }
            )
    text = _flatten_text([b for b in blocks if b.get("type") == "text"])
    if text:
        translated.append({"role": "user", "content": text})
    return translated


def _translate_tool_choice(choice: Any) -> Any:
    if not isinstance(choice, dict):
        return None
    kind = choice.get("type")
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    if kind == "tool":
        return {"type": "function", "function": {"name": choice["name"]}}
    return None


def to_openai_request(body: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
    """Anthropic /v1/messages request -> OpenAI /chat/completions request."""
    messages: list[dict[str, Any]] = []
    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _flatten_text(system)})
    for message in body.get("messages", []):
        messages.extend(_translate_message(message))

    request: dict[str, Any] = {
        "model": model or body.get("model", ""),
        "messages": messages,
    }
    if "max_tokens" in body:
        request["max_tokens"] = body["max_tokens"]
    for field in ("temperature", "top_p"):
        if field in body:
            request[field] = body[field]
    if body.get("stop_sequences"):
        request["stop"] = body["stop_sequences"]
    tools = body.get("tools")
    if tools:
        request["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]
        choice = _translate_tool_choice(body.get("tool_choice"))
        if choice is not None:
            request["tool_choice"] = choice
    return request


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Models occasionally emit malformed tool JSON. Surface it rather than
        # dropping the call: the agent's handling of it is part of what is measured.
        return {"_raw_arguments": str(raw)}
    return parsed if isinstance(parsed, dict) else {"_value": parsed}


def from_openai_response(response: dict[str, Any]) -> dict[str, Any]:
    """OpenAI chat completion -> Anthropic messages response."""
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}

    content: list[dict[str, Any]] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        content.append(
            {
                "type": "tool_use",
                "id": call.get("id", ""),
                "name": function.get("name", ""),
                "input": _parse_arguments(function.get("arguments")),
            }
        )

    usage = response.get("usage") or {}
    stop_reason = _STOP_REASONS.get(str(choice.get("finish_reason")), "end_turn")
    if any(block["type"] == "tool_use" for block in content):
        stop_reason = "tool_use"

    return {
        "id": response.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": response.get("model", ""),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
        },
    }
