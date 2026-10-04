"""Protocol checks against redacted real provider responses.

The tool fixture came from .runtime/supplier-validation-20260908/response-02.json.
The text fixture came from .runtime/next-eval/current-model-20260926/response-02.json;
its opaque response ID and reasoning content were redacted, while null field
shapes and usage were retained.
"""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from len_bot.next.models.client import (
    ChatModel,
    ModelHTTPError,
    ModelProtocolError,
    ModelSettings,
    parse_chat_completion,
)


RECORDED_RESPONSE = Path(__file__).parent / "fixtures/next/model/response-02.json"
RECORDED_TEXT_RESPONSE = Path(__file__).parent / "fixtures/next/model/response-text.json"


def recorded_response() -> dict:
    return json.loads(RECORDED_RESPONSE.read_text())


def recorded_text_response() -> dict:
    return json.loads(RECORDED_TEXT_RESPONSE.read_text())


def test_recorded_tool_call_keeps_native_continuation_and_usage():
    body = recorded_response()
    reply = parse_chat_completion(body)

    assert reply.message == body["choices"][0]["message"]
    assert reply.message is not body["choices"][0]["message"]
    assert reply.text == ""
    assert reply.finish_reason == "tool_calls"
    assert [(call.id, call.name, call.arguments) for call in reply.tool_calls] == [
        (
            "call_13d8afaf8eb845bc90f2b5fb231b97d5",
            "read_validation_document",
            {},
        )
    ]
    assert reply.usage == body["usage"]
    assert reply.usage["completion_tokens_details"]["reasoning_tokens"] == 374
    assert (reply.token_usage.prompt_tokens, reply.token_usage.completion_tokens,
            reply.token_usage.cached_tokens) == (3946, 388, 0)
    assert body["usage"]["total_tokens"] == 4333  # Retained, not forced to match the components.


def test_recorded_response_with_injected_native_extension_keeps_it_unchanged():
    body = recorded_response()
    message = body["choices"][0]["message"]
    message["provider_continuation"] = {"signature": "replay-only", "blocks": [None, 1]}
    reply = parse_chat_completion(body)
    assert reply.message == message
    assert reply.message["provider_continuation"] is not message["provider_continuation"]


def test_recorded_text_response_accepts_null_tool_calls_and_preserves_native_extras():
    body = recorded_text_response()
    message = body["choices"][0]["message"]
    assert message["tool_calls"] is None
    assert message["reasoning_content"] == "[redacted]"

    reply = parse_chat_completion(body)
    assert reply.text == message["content"]
    assert reply.tool_calls == []
    assert reply.finish_reason == "stop"
    assert reply.usage == body["usage"]
    assert (reply.token_usage.prompt_tokens, reply.token_usage.completion_tokens,
            reply.token_usage.cached_tokens) == (1351, 382, None)
    assert reply.message == message
    assert reply.message is not message


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda body: body["choices"][0].update(finish_reason="length"), "finish_reason"),
        (
            lambda body: body["choices"][0]["message"]["tool_calls"][0]["function"].update(
                arguments='{"offset":'
            ),
            "tool arguments",
        ),
        (
            lambda body: body["choices"][0]["message"]["tool_calls"][0]["function"].update(
                arguments='{"offset":NaN}'
            ),
            "non-standard JSON constant",
        ),
        (
            lambda body: body["choices"][0]["message"]["tool_calls"][0]["function"].update(
                arguments='{"offset":Infinity}'
            ),
            "non-standard JSON constant",
        ),
    ],
)
def test_recorded_response_fault_injections_fail_before_tool_execution(change, reason):
    body = recorded_response()
    change(body)
    with pytest.raises(ModelProtocolError) as failure:
        parse_chat_completion(body)
    assert reason in str(failure.value)
    assert "response fragment:" in str(failure.value)
    assert failure.value.response == body
    assert failure.value.response is not body
    assert failure.value.usage == body["usage"]
    assert failure.value.token_usage.prompt_tokens == body["usage"]["prompt_tokens"]


def test_missing_usage_in_recorded_response_is_unknown_not_zero():
    body = recorded_response()
    del body["usage"]
    reply = parse_chat_completion(body)
    assert reply.usage is None
    assert reply.token_usage is None


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda usage: usage.update(prompt_tokens=True), "prompt_tokens"),
        (lambda usage: usage.update(prompt_tokens=-1), "prompt_tokens"),
        (lambda usage: usage.update(completion_tokens=1.5), "completion_tokens"),
        (lambda usage: usage.update(completion_tokens="388"), "completion_tokens"),
        (lambda usage: usage["prompt_tokens_details"].update(cached_tokens=3947), "cached_tokens"),
        (lambda usage: usage["prompt_tokens_details"].update(cached_tokens=False), "cached_tokens"),
        (lambda usage: usage.update(prompt_tokens_details=[]), "prompt_tokens_details"),
    ],
)
def test_recorded_usage_faults_reject_without_losing_raw_response(change, field):
    body = recorded_response()
    change(body["usage"])
    with pytest.raises(ModelProtocolError) as failure:
        parse_chat_completion(body)
    assert field in str(failure.value)
    assert failure.value.response == body
    assert failure.value.usage == body["usage"]
    assert failure.value.token_usage is None


def test_recorded_usage_null_fields_remain_unknown_without_alias_guessing():
    body = recorded_response()
    body["usage"]["prompt_tokens"] = None
    body["usage"]["completion_tokens"] = None
    body["usage"]["prompt_tokens_details"] = None
    reply = parse_chat_completion(body)
    assert (reply.token_usage.prompt_tokens, reply.token_usage.completion_tokens,
            reply.token_usage.cached_tokens) == (None, None, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api", "anthropic-messages"),
        ("base_url", ""),
        ("api_key", " "),
        ("model", ""),
        ("reasoning_effort", " "),
        ("max_output_tokens", 0),
        ("timeout_seconds", 0),
    ],
)
def test_model_settings_reject_invalid_config_at_named_field(field, value):
    source = {
        "api": "openai-chat",
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "replay-only",
        "model": "recorded-model",
    }
    source[field] = value
    with pytest.raises(ValidationError) as failure:
        ModelSettings.model_validate(source)
    assert field in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["native", "antigravity-chat"])
async def test_loopback_replay_sends_native_tool_continuation_without_retry(tmp_path, policy):
    recorded = recorded_response()
    recorded_text = recorded_text_response()
    requests = []
    recorded["choices"][0]["message"]["provider_continuation"] = {"signature": "synthetic-opaque"}

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        headers = await reader.readuntil(b"\r\n\r\n")
        request_line, *header_lines = headers.decode("ascii").split("\r\n")
        fields = {
            key.lower(): value.strip()
            for line in header_lines
            if ":" in line
            for key, value in [line.split(":", 1)]
        }
        payload = json.loads(await reader.readexactly(int(fields["content-length"])))
        requests.append((request_line, fields, payload))
        body = json.dumps(recorded if len(requests) == 1 else recorded_text).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        settings = ModelSettings(
            api="openai-chat", base_url=f"http://127.0.0.1:{port}/v1",
            api_key="replay-only", model="recorded-model", reasoning_effort="high",
        )
        assert settings.temperature == 0.6
        assert settings.max_output_tokens == 1024
        assert settings.timeout_seconds == 60
        tools = [{"type": "function", "function": {"name": "read_validation_document", "parameters": {"type": "object"}}}]
        messages = [{"role": "user", "content": "Read the document"}]
        from len_bot.next.config import LabConfig
        from len_bot.next.storage.store import Store
        from len_bot.next.models.request import request_model
        config = LabConfig.model_validate_json(json.dumps({
            "mode": "isolated", "scene": "group:80001", "bot_qq": "90001", "timezone": "UTC",
            "database": str(tmp_path / "chat.sqlite3"), "persona": str(tmp_path / "persona"),
            "models": {"providers": {"fixture": {"api": settings.api, "base_url": settings.base_url,
                                               "api_key": settings.api_key}},
                       "roles": {"mind": {"provider": "fixture", "model": settings.model,
                           "context_window_tokens": 200000, "history_policy": policy,
                           "reasoning_effort": "high"}}},
        }))
        async with ChatModel(settings) as model:
            with Store(config.database) as store:
                turn = store.start_turn(config.scene)
                first = await request_model(config, store, model, messages, tools,
                                            scene=config.scene, role="mind", turn_id=turn)
                continuation = messages + [first.message, {
                    "role": "tool", "tool_call_id": first.tool_calls[0].id, "content": "document excerpt",
                }]
                second = await request_model(config, store, model, continuation, tools,
                    scene=config.scene, role="mind", turn_id=turn, output_tokens=512)
                await request_model(config, store, model, continuation + [second.message, {
                    "role": "user", "content": "continue",
                }], tools, scene=config.scene, role="mind", turn_id=turn)
                raw = json.loads(store.db.execute("SELECT response FROM model_calls ORDER BY id LIMIT 1").fetchone()[0])
                assert raw["raw"] == recorded
        assert settings.max_output_tokens == 1024

    assert len(requests) == 3
    assert all(line == "POST /v1/chat/completions HTTP/1.1" for line, _, _ in requests)
    assert all(headers["authorization"] == "Bearer replay-only" for _, headers, _ in requests)
    first_payload = requests[0][2]
    assert first_payload["stream"] is False
    assert first_payload["model"] == "recorded-model"
    assert first_payload["temperature"] == 0.6
    assert first_payload["max_completion_tokens"] == 1024
    assert requests[1][2]["max_completion_tokens"] == 512
    assert requests[2][2]["max_completion_tokens"] == 1024
    assert first_payload["reasoning_effort"] == "high"
    assert first_payload["tools"] == tools
    native = recorded["choices"][0]["message"]
    assert requests[1][2]["messages"][1] == {key: value for key, value in native.items()
        if policy == "native" or key != "reasoning_content"}
    assert requests[1][2]["messages"][1]["provider_continuation"] == {"signature": "synthetic-opaque"}
    if policy == "antigravity-chat":
        assert all(headers["session-id"] == requests[0][1]["session-id"] for _, headers, _ in requests)
    else:
        assert all("session-id" not in headers for _, headers, _ in requests)
    assert requests[1][2]["messages"][2]["tool_call_id"] == first.tool_calls[0].id
    native = recorded_text["choices"][0]["message"]
    assert requests[2][2]["messages"][-2] == {key: value for key, value in native.items()
        if policy == "native" or key != "reasoning_content"}
    assert requests[2][2]["messages"][-2]["tool_calls"] is None
    if policy == "native":
        assert requests[2][2]["messages"][-2]["reasoning_content"] == "[redacted]"
    else:
        assert "reasoning_content" not in requests[2][2]["messages"][-2]


@pytest.mark.asyncio
async def test_loopback_http_error_propagates_original_body_without_retry():
    requests = 0
    payloads = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal requests
        headers = await reader.readuntil(b"\r\n\r\n")
        for line in headers.decode("ascii").split("\r\n"):
            if line.lower().startswith("content-length:"):
                payloads.append(json.loads(await reader.readexactly(int(line.split(":", 1)[1]))))
        requests += 1
        body = b'{"error":{"message":"replay rate limit"}}'
        writer.write(
            b"HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        settings = ModelSettings(
            api="openai-chat", base_url=f"http://127.0.0.1:{port}/v1",
            api_key="replay-only", model="recorded-model",
        )
        async with ChatModel(settings) as model:
            with pytest.raises(ModelHTTPError, match="replay rate limit") as failure:
                await model.complete([{"role": "user", "content": "hello"}], [])
    assert "429" in str(failure.value)
    assert requests == 1
    assert "reasoning_effort" not in payloads[0]


@pytest.mark.asyncio
async def test_loopback_truncated_recorded_json_has_unknown_usage():
    recorded_bytes = RECORDED_RESPONSE.read_bytes()
    truncated = recorded_bytes[:recorded_bytes.index(b'"usage"')]

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        headers = await reader.readuntil(b"\r\n\r\n")
        for line in headers.decode("ascii").split("\r\n"):
            if line.lower().startswith("content-length:"):
                await reader.readexactly(int(line.split(":", 1)[1]))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(truncated)}\r\nConnection: close\r\n\r\n".encode()
            + truncated
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        settings = ModelSettings(
            api="openai-chat", base_url=f"http://127.0.0.1:{port}/v1",
            api_key="replay-only", model="recorded-model",
        )
        async with ChatModel(settings) as model:
            with pytest.raises(ModelProtocolError, match="Invalid chat completion JSON") as failure:
                await model.complete([{"role": "user", "content": "hello"}], [])
    assert failure.value.usage is None
    assert failure.value.response == truncated.decode()


def test_recorded_summary_code_fence_is_not_silently_repaired():
    from len_bot.next.memory.summary import parse_summary
    recorded = json.loads((RECORDED_RESPONSE.parent / 'summary-fenced-response.json').read_text())
    reply = parse_chat_completion({'choices': [recorded]})
    with pytest.raises(ValueError, match='Invalid memory summary response.*response fragment'):
        parse_summary(reply)
