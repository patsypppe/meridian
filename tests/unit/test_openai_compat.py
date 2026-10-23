"""Anthropic <-> OpenAI translation for the proxy — pure functions, no network."""

from __future__ import annotations

import json

import httpx
import pytest

from meridian.config import ProxyMode
from meridian.runtime.proxy import server as server_module
from meridian.runtime.proxy.openai_compat import from_openai_response, to_openai_request
from meridian.runtime.proxy.server import (
    UPSTREAM_API_OPENAI,
    ProxyConfig,
    config_from_env,
    create_app,
)
from meridian.runtime.secrets import redact

pytestmark = pytest.mark.unit

ANTHROPIC_REQUEST = {
    "model": "llama-3.3-70b-versatile",
    "max_tokens": 256,
    "temperature": 0,
    "system": [{"type": "text", "text": "You are a checkout agent."}],
    "tools": [
        {
            "name": "apply_coupon",
            "description": "Apply a coupon",
            "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}},
        }
    ],
    "tool_choice": {"type": "any"},
    "messages": [
        {"role": "user", "content": "apply SAVE10"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "ok"},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "apply_coupon",
                    "input": {"code": "SAVE10"},
                },
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "applied"}],
        },
    ],
}


def test_request_translation_maps_system_tools_and_results() -> None:
    out = to_openai_request(ANTHROPIC_REQUEST)
    assert out["messages"][0] == {"role": "system", "content": "You are a checkout agent."}
    assert out["messages"][1] == {"role": "user", "content": "apply SAVE10"}
    assistant = out["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "apply_coupon"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"code": "SAVE10"}
    assert out["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "applied"}
    assert out["tools"][0]["function"]["parameters"]["type"] == "object"
    assert out["tool_choice"] == "required"
    assert out["max_tokens"] == 256 and out["temperature"] == 0


def test_request_translation_is_deterministic() -> None:
    assert to_openai_request(ANTHROPIC_REQUEST) == to_openai_request(ANTHROPIC_REQUEST)


def test_response_translation_text_and_usage() -> None:
    out = from_openai_response(
        {
            "id": "c1",
            "model": "m",
            "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        }
    )
    assert out["content"] == [{"type": "text", "text": "done"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {"input_tokens": 11, "output_tokens": 3}


def test_response_translation_tool_call_sets_tool_use() -> None:
    out = from_openai_response(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "x",
                                "type": "function",
                                "function": {"name": "f", "arguments": '{"a": 1}'},
                            }
                        ],
                    },
                    "finish_reason": "stop",
                }
            ]
        }
    )
    assert out["stop_reason"] == "tool_use"
    assert out["content"] == [{"type": "tool_use", "id": "x", "name": "f", "input": {"a": 1}}]


def test_malformed_tool_arguments_are_surfaced_not_dropped() -> None:
    out = from_openai_response(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [{"id": "x", "function": {"name": "f", "arguments": "{oops"}}]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    assert out["content"][0]["input"] == {"_raw_arguments": "{oops"}


def test_openai_upstream_requires_a_credential() -> None:
    config = config_from_env(
        {
            "MERIDIAN_UPSTREAM_API": "openai",
            "MERIDIAN_UPSTREAM_BASE_URL": "https://api.groq.com/openai/v1",
        }
    )
    assert config.upstream_api == UPSTREAM_API_OPENAI
    assert config.requires_credential


def test_groq_keys_are_redacted() -> None:
    assert "gsk_abcdefghijklmnop" not in redact("upstream rejected gsk_abcdefghijklmnop")


async def test_proxy_translates_on_the_wire(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        server_module.httpx,
        "AsyncClient",
        lambda **kw: real_client(**{"transport": httpx.MockTransport(handler), **kw}),
    )
    from meridian.runtime.secrets import ProviderCredential

    app = create_app(
        ProxyConfig(
            mode=ProxyMode.RECORD,
            cassette_dir=tmp_path,  # type: ignore[arg-type]
            limits={},
            upstream_base_url="https://api.groq.com/openai/v1",
            upstream_api=UPSTREAM_API_OPENAI,
            upstream_model="llama-3.3-70b-versatile",
            credential=ProviderCredential(env_var="GROQ_API_KEY", value="gsk_test_key_value"),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json={"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "x"}]},
            headers={"x-meridian-task": "happy-path", "x-meridian-trial": "0"},
        )
    assert response.status_code == 200
    assert response.json()["content"] == [{"type": "text", "text": "hi"}]
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer gsk_test_key_value"
    assert seen["body"]["model"] == "llama-3.3-70b-versatile"  # type: ignore[index]
    assert seen["body"]["messages"] == [{"role": "user", "content": "x"}]  # type: ignore[index]


async def test_anthropic_upstream_model_override(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "type": "message",
                "content": [],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        server_module.httpx,
        "AsyncClient",
        lambda **kw: real_client(**{"transport": httpx.MockTransport(handler), **kw}),
    )
    from meridian.runtime.secrets import ProviderCredential

    app = create_app(
        ProxyConfig(
            mode=ProxyMode.RECORD,
            cassette_dir=tmp_path,  # type: ignore[arg-type]
            limits={},
            upstream_model="claude-haiku-4-5-20251001",
            credential=ProviderCredential(env_var="ANTHROPIC_API_KEY", value="sk-ant-test-value"),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy"
    ) as client:
        await client.post(
            "/v1/messages",
            json={"model": "agent-chose-this", "max_tokens": 8, "messages": []},
            headers={"x-meridian-task": "happy-path", "x-meridian-trial": "0"},
        )
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["body"]["model"] == "claude-haiku-4-5-20251001"  # type: ignore[index]


def test_retry_delay_honours_retry_after_and_caps() -> None:
    assert server_module.retry_delay({"retry-after": "3"}, 0) == 3.0
    assert server_module.retry_delay({}, 2) == 4.0
    assert server_module.retry_delay({"retry-after": "999"}, 0) == 90.0
    assert server_module.retry_delay({"retry-after": "soon"}, 1) == 2.0


async def test_transient_provider_errors_are_retried_not_scored(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "rate"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async def no_sleep(_: float) -> None:
        return None

    real_client = httpx.AsyncClient
    monkeypatch.setattr(server_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        server_module.httpx,
        "AsyncClient",
        lambda **kw: real_client(**{"transport": httpx.MockTransport(handler), **kw}),
    )
    from meridian.runtime.secrets import ProviderCredential

    app = create_app(
        ProxyConfig(
            mode=ProxyMode.RECORD,
            cassette_dir=tmp_path,  # type: ignore[arg-type]
            limits={},
            upstream_base_url="https://api.groq.com/openai/v1",
            upstream_api=UPSTREAM_API_OPENAI,
            credential=ProviderCredential(env_var="GROQ_API_KEY", value="gsk_test_key_value"),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json={"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "x"}]},
            headers={"x-meridian-task": "happy-path", "x-meridian-trial": "0"},
        )
    assert response.status_code == 200
    assert calls["n"] == 3
