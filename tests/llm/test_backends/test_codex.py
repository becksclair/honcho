from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import BaseModel

from src.exceptions import ValidationException
from src.llm.backends.codex import (
    CodexResponsesBackend,
    CodexResponsesClient,
    build_codex_responses_request,
    codex_responses_url,
    convert_messages_to_responses_input,
    convert_tool_choice,
    convert_tools_to_responses,
    normalize_codex_base_url,
    normalize_codex_response,
)
from src.llm.codex_auth import CodexAuthManager
from tests.codex_test_helpers import codex_jwt, write_codex_auth


class FakeCodexClient:
    response: dict[str, Any]
    bodies: list[dict[str, Any]]

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.bodies = []

    async def create_response(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        return self.response


class StructuredAnswer(BaseModel):
    answer: str


def _write_auth(path: Path, *, access_token: str | None = None) -> None:
    write_codex_auth(
        path,
        access_token=access_token,
        account_id="fake-account",
        id_token=None,
    )


def test_codex_responses_url_normalizes_base_url() -> None:
    assert (
        codex_responses_url("https://chatgpt.com/backend-api/codex")
        == "https://chatgpt.com/backend-api/codex/responses"
    )
    assert (
        codex_responses_url("https://chatgpt.com/backend-api/codex/responses")
        == "https://chatgpt.com/backend-api/codex/responses"
    )
    assert (
        normalize_codex_base_url("https://chatgpt.com/backend-api/codex/responses/")
        == "https://chatgpt.com/backend-api/codex"
    )


def test_convert_messages_to_responses_input_preserves_tool_replay() -> None:
    instructions, input_items = convert_messages_to_responses_input(
        [
            {"role": "system", "content": "System guardrail"},
            {"role": "user", "content": "Please look this up"},
            {
                "role": "assistant",
                "content": "Calling the tool",
                "tool_calls": [
                    {
                        "id": "call_lookup|fc_lookup",
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "arguments": '{"query":"codex"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_lookup|fc_lookup",
                "content": "tool says yes",
            },
        ]
    )

    assert instructions == "System guardrail"
    assert input_items == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Please look this up"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Calling the tool"}],
        },
        {
            "type": "function_call",
            "call_id": "call_lookup",
            "name": "lookup",
            "arguments": '{"query":"codex"}',
            "id": "fc_lookup",
        },
        {
            "type": "function_call_output",
            "call_id": "call_lookup",
            "output": "tool says yes",
        },
    ]


def test_convert_tools_and_tool_choice_to_responses_shape() -> None:
    tools = convert_tools_to_responses(
        [
            {
                "name": "lookup",
                "description": "Lookup a test value",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]
    )

    assert tools == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Lookup a test value",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "strict": None,
        }
    ]
    assert convert_tool_choice("any") == "required"
    assert convert_tool_choice(None) == "auto"
    assert convert_tool_choice(
        {"type": "function", "function": {"name": "lookup"}}
    ) == {"type": "function", "name": "lookup"}
    assert convert_tool_choice({"type": "tool", "name": "lookup"}) == {
        "type": "function",
        "name": "lookup",
    }
    assert convert_tool_choice("lookup") == {"type": "function", "name": "lookup"}


def test_build_codex_responses_request_sets_responses_fields() -> None:
    body = build_codex_responses_request(
        model="gpt-5.5",
        messages=[{"role": "user", "content": "Hello"}],
        tools=[
            {
                "name": "lookup",
                "description": "Lookup a test value",
                "input_schema": {"type": "object"},
            }
        ],
        tool_choice="required",
        thinking_effort="medium",
        extra_params={"verbosity": "medium", "prompt_cache_key": "test-key"},
    )

    assert body["model"] == "gpt-5.5"
    assert body["store"] is False
    assert body["stream"] is True
    assert body["instructions"] == "You are a concise assistant."
    assert "max_output_tokens" not in body
    assert body["text"]["verbosity"] == "medium"
    assert body["reasoning"] == {"effort": "medium"}
    assert body["tool_choice"] == "required"
    assert body["parallel_tool_calls"] is True
    assert body["prompt_cache_key"] == "test-key"


def test_build_codex_responses_lite_request_uses_lite_contract() -> None:
    body = build_codex_responses_request(
        model="gpt-5.6-luna",
        messages=[
            {"role": "system", "content": "System guardrail"},
            {"role": "user", "content": "Hello"},
        ],
        tools=[
            {
                "name": "lookup",
                "description": "Lookup a test value",
                "input_schema": {"type": "object"},
            }
        ],
        tool_choice="auto",
        thinking_effort="max",
    )

    assert "instructions" not in body
    assert "tools" not in body
    assert body["parallel_tool_calls"] is False
    assert body["tool_choice"] == "auto"
    assert body["reasoning"] == {"effort": "max", "context": "all_turns"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["input"][0]["type"] == "additional_tools"
    assert body["input"][0]["tools"][0]["name"] == "lookup"
    assert body["input"][1] == {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": "System guardrail"}],
    }


@pytest.mark.parametrize("thinking_effort", ["minimal", "low"])
def test_build_codex_responses_request_rejects_cheap_reasoning_for_gpt55(
    thinking_effort: str,
) -> None:
    with pytest.raises(ValidationException, match="must not use minimal or low"):
        build_codex_responses_request(
            model="gpt-5.5",
            messages=[{"role": "user", "content": "Hello"}],
            thinking_effort=thinking_effort,
        )


def test_build_codex_responses_request_rejects_unsupported_params() -> None:
    with pytest.raises(ValidationException, match="frequency_penalty"):
        build_codex_responses_request(
            model="gpt-5.5",
            messages=[{"role": "user", "content": "Hello"}],
            extra_params={"frequency_penalty": 0.2},
        )


def test_normalize_codex_response_extracts_text_tool_calls_and_usage() -> None:
    result = normalize_codex_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Need lookup"}],
                },
                {
                    "type": "function_call",
                    "id": "fc_123",
                    "call_id": "call_123",
                    "name": "lookup",
                    "arguments": '{"query":"codex"}',
                },
            ],
            "usage": {
                "input_tokens": 40,
                "output_tokens": 12,
                "input_tokens_details": {"cached_tokens": 7},
            },
        }
    )

    assert result.content == "Need lookup"
    assert result.input_tokens == 40
    assert result.output_tokens == 12
    assert result.cache_read_input_tokens == 7
    assert result.finish_reason == "tool_calls"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_123|fc_123"
    assert result.tool_calls[0].name == "lookup"
    assert result.tool_calls[0].input == {"query": "codex"}


def test_normalize_codex_response_logs_malformed_tool_args(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="src.llm.backends.codex")

    result = normalize_codex_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_123",
                    "call_id": "call_123",
                    "name": "lookup",
                    "arguments": '{"secret":"should-not-log"',
                },
            ],
        }
    )

    assert result.tool_calls[0].input == {}
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "invalid JSON" in logged
    assert "lookup" in logged
    assert "JSONDecodeError" in logged
    assert "should-not-log" not in logged


def test_normalize_codex_response_rejects_failed_status() -> None:
    with pytest.raises(ValidationException, match="failed"):
        normalize_codex_response({"status": "failed", "output": []})


def test_normalize_codex_response_rejects_incomplete_status() -> None:
    with pytest.raises(ValidationException, match="max_output_tokens"):
        normalize_codex_response(
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            }
        )


def test_normalize_codex_response_does_not_duplicate_top_output_text() -> None:
    result = normalize_codex_response(
        {
            "status": "completed",
            "output_text": "hello",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "hello"}],
                }
            ],
        }
    )

    assert result.content == "hello"


def test_normalize_codex_response_uses_single_id_when_call_id_is_missing() -> None:
    result = normalize_codex_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_only",
                    "name": "lookup",
                    "arguments": '{"query":"codex"}',
                },
            ],
        }
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "fc_only"


@pytest.mark.asyncio
async def test_codex_backend_sends_request_and_validates_structured_output() -> None:
    client = FakeCodexClient(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": '{"answer":"ok"}'},
                    ],
                }
            ],
            "usage": {"input_tokens": 8, "output_tokens": 4},
        }
    )
    backend = CodexResponsesBackend(cast(Any, client))

    result = await backend.complete(
        model="gpt-5.5",
        messages=[{"role": "user", "content": "Return JSON"}],
        max_tokens=64,
        response_format=StructuredAnswer,
    )

    assert isinstance(result.content, StructuredAnswer)
    assert result.content.answer == "ok"
    assert client.bodies[0]["text"]["format"]["type"] == "json_schema"
    assert "max_output_tokens" not in client.bodies[0]


@pytest.mark.asyncio
async def test_codex_backend_rejects_unsupported_stop_sequences() -> None:
    backend = CodexResponsesBackend(cast(Any, FakeCodexClient({"status": "completed"})))

    with pytest.raises(ValidationException, match="stop sequences"):
        await backend.complete(
            model="gpt-5.5",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=64,
            stop=["END"],
        )


@pytest.mark.asyncio
async def test_codex_client_aggregates_streaming_response(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    sse = "\n\n".join(
        [
            "event: response.output_text.delta\n"
            + 'data: {"type":"response.output_text.delta","delta":"he"}',
            "event: response.output_text.delta\n"
            + 'data: {"type":"response.output_text.delta","delta":"llo"}',
            "event: response.output_item.done\n"
            + 'data: {"type":"response.output_item.done","item":{"type":"message",'
            + '"content":[{"type":"output_text","text":"hello"}]}}',
            "event: response.completed\n"
            + 'data: {"type":"response.completed","response":{"status":"completed",'
            + '"output":[],'
            + '"usage":{"input_tokens":3,"output_tokens":1}}}',
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["chatgpt-account-id"] == "fake-account"
        assert request.headers["x-openai-internal-codex-responses-lite"] == "true"
        assert "OpenAI-Beta" not in request.headers
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = CodexResponsesClient(
            auth_manager=CodexAuthManager(auth_file=auth_file),
            http_client=http_client,
        )
        response = await client.create_response(
            {"model": "gpt-5.6-luna", "stream": True}
        )

    assert response["status"] == "completed"
    assert response["output"][0]["type"] == "message"
    assert response["usage"]["input_tokens"] == 3
    assert normalize_codex_response(response).content == "hello"


@pytest.mark.asyncio
async def test_codex_client_rejects_stream_without_completion_event(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    sse = (
        "event: response.output_text.delta\n"
        + 'data: {"type":"response.output_text.delta","delta":"partial"}'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = CodexResponsesClient(
            auth_manager=CodexAuthManager(auth_file=auth_file),
            http_client=http_client,
        )
        with pytest.raises(ValidationException, match="without completion event"):
            await client.create_response({"stream": True})


@pytest.mark.asyncio
async def test_codex_client_rejects_incomplete_sse_event(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    sse = "\n\n".join(
        [
            "event: response.output_text.delta\n"
            + 'data: {"type":"response.output_text.delta","delta":"partial"}',
            "event: response.incomplete\n"
            + 'data: {"type":"response.incomplete","response":{"status":"incomplete",'
            + '"incomplete_details":{"reason":"max_output_tokens"}}}',
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = CodexResponsesClient(
            auth_manager=CodexAuthManager(auth_file=auth_file),
            http_client=http_client,
        )
        with pytest.raises(ValidationException, match="max_output_tokens"):
            await client.create_response({"stream": True})


@pytest.mark.asyncio
async def test_codex_backend_stream_yields_deltas_and_final_usage(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    sse = "\n\n".join(
        [
            "event: response.output_text.delta\n"
            + 'data: {"type":"response.output_text.delta","delta":"he"}',
            "event: response.output_text.delta\n"
            + 'data: {"type":"response.output_text.delta","delta":"llo"}',
            "event: response.completed\n"
            + 'data: {"type":"response.completed","response":{"status":"completed",'
            + '"usage":{"input_tokens":3,"output_tokens":2}}}',
        ]
    )
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        backend = CodexResponsesBackend(
            CodexResponsesClient(
                auth_manager=CodexAuthManager(auth_file=auth_file),
                http_client=http_client,
            )
        )
        chunks = [
            chunk
            async for chunk in backend.stream(
                model="gpt-5.5",
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=64,
            )
        ]

    assert [chunk.content for chunk in chunks] == ["he", "llo", ""]
    assert chunks[-1].is_done is True
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].output_tokens == 2
    assert "max_output_tokens" not in requests[0]


@pytest.mark.asyncio
async def test_codex_backend_stream_rejects_incomplete_completion(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    sse = "\n\n".join(
        [
            "event: response.output_text.delta\n"
            + 'data: {"type":"response.output_text.delta","delta":"partial"}',
            "event: response.completed\n"
            + 'data: {"type":"response.completed","response":{"status":"incomplete",'
            + '"incomplete_details":{"reason":"max_output_tokens"}}}',
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        backend = CodexResponsesBackend(
            CodexResponsesClient(
                auth_manager=CodexAuthManager(auth_file=auth_file),
                http_client=http_client,
            )
        )
        stream = backend.stream(
            model="gpt-5.5",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=64,
        )
        first = await anext(stream)
        assert first.content == "partial"
        with pytest.raises(ValidationException, match="max_output_tokens"):
            await anext(stream)


@pytest.mark.asyncio
async def test_codex_backend_stream_rejects_incomplete_sse_event(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    sse = "\n\n".join(
        [
            "event: response.output_text.delta\n"
            + 'data: {"type":"response.output_text.delta","delta":"partial"}',
            "event: response.incomplete\n"
            + 'data: {"type":"response.incomplete","response":{"status":"incomplete",'
            + '"incomplete_details":{"reason":"max_output_tokens"}}}',
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        backend = CodexResponsesBackend(
            CodexResponsesClient(
                auth_manager=CodexAuthManager(auth_file=auth_file),
                http_client=http_client,
            )
        )
        stream = backend.stream(
            model="gpt-5.5",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=64,
        )
        first = await anext(stream)
        assert first.content == "partial"
        with pytest.raises(ValidationException, match="max_output_tokens"):
            await anext(stream)


@pytest.mark.asyncio
async def test_codex_backend_stream_rejects_missing_completion_event(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    sse = (
        "event: response.output_text.delta\n"
        + 'data: {"type":"response.output_text.delta","delta":"partial"}'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        backend = CodexResponsesBackend(
            CodexResponsesClient(
                auth_manager=CodexAuthManager(auth_file=auth_file),
                http_client=http_client,
            )
        )
        with pytest.raises(ValidationException, match="without completion event"):
            async for _chunk in backend.stream(
                model="gpt-5.5",
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=64,
            ):
                pass


@pytest.mark.asyncio
async def test_codex_client_merges_streamed_items_with_completed_output(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    sse = "\n\n".join(
        [
            "event: response.output_item.done\n"
            + 'data: {"type":"response.output_item.done","item":'
            + '{"type":"function_call","id":"fc_lookup","call_id":"call_lookup",'
            + '"name":"lookup","arguments":"{}"}}',
            "event: response.completed\n"
            + 'data: {"type":"response.completed","response":{"status":"completed",'
            + '"output":[{"type":"message","content":[{"type":"output_text",'
            + '"text":"hello"}]}]}}',
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = CodexResponsesClient(
            auth_manager=CodexAuthManager(auth_file=auth_file),
            http_client=http_client,
        )
        response = await client.create_response({"stream": True})

    assert [item["type"] for item in response["output"]] == [
        "message",
        "function_call",
    ]


@pytest.mark.asyncio
async def test_codex_client_http_error_does_not_echo_provider_body(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"detail": "raw prompt body should not be surfaced"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = CodexResponsesClient(
            auth_manager=CodexAuthManager(auth_file=auth_file),
            http_client=http_client,
        )
        with pytest.raises(ValidationException) as exc_info:
            await client.create_response({"stream": True})

    message = str(exc_info.value)
    assert "HTTP 400" in message
    assert "raw prompt body" not in message


@pytest.mark.asyncio
async def test_codex_client_retries_nonstream_after_auth_refresh(
    tmp_path: Path,
) -> None:
    old_token = codex_jwt(time.time() + 3600)
    new_token = codex_jwt(time.time() + 7200)
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file, access_token=old_token)
    response_calls = 0
    refresh_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_calls, response_calls
        if request.url.host == "auth.openai.com":
            refresh_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": new_token,
                    "refresh_token": "new-refresh-token",
                },
            )

        response_calls += 1
        if response_calls == 1:
            assert request.headers["authorization"] == f"Bearer {old_token}"
            return httpx.Response(
                401,
                json={"detail": "raw prompt body should not be surfaced"},
            )

        assert request.headers["authorization"] == f"Bearer {new_token}"
        return httpx.Response(200, json={"status": "completed", "output_text": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = CodexResponsesClient(
            auth_manager=CodexAuthManager(auth_file=auth_file, http_client=http_client),
            http_client=http_client,
        )
        response = await client.create_response({"stream": False})

    assert response == {"status": "completed", "output_text": "ok"}
    assert response_calls == 2
    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_codex_backend_complete_retries_after_auth_refresh(
    tmp_path: Path,
) -> None:
    old_token = codex_jwt(time.time() + 3600)
    new_token = codex_jwt(time.time() + 7200)
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file, access_token=old_token)
    response_calls = 0
    refresh_calls = 0
    sse = (
        "event: response.completed\n"
        + 'data: {"type":"response.completed","response":{"status":"completed",'
        + '"output_text":"ok","usage":{"output_tokens":1}}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_calls, response_calls
        if request.url.host == "auth.openai.com":
            refresh_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": new_token,
                    "refresh_token": "new-refresh-token",
                },
            )

        response_calls += 1
        if response_calls == 1:
            assert request.headers["authorization"] == f"Bearer {old_token}"
            return httpx.Response(401, json={"detail": "token was revoked"})

        assert request.headers["authorization"] == f"Bearer {new_token}"
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        backend = CodexResponsesBackend(
            CodexResponsesClient(
                auth_manager=CodexAuthManager(
                    auth_file=auth_file,
                    http_client=http_client,
                ),
                http_client=http_client,
            )
        )
        result = await backend.complete(
            model="gpt-5.5",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=64,
        )

    assert result.content == "ok"
    assert result.finish_reason == "stop"
    assert result.output_tokens == 1
    assert response_calls == 2
    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_codex_backend_stream_retries_after_auth_refresh(
    tmp_path: Path,
) -> None:
    old_token = codex_jwt(time.time() + 3600)
    new_token = codex_jwt(time.time() + 7200)
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file, access_token=old_token)
    response_calls = 0
    refresh_calls = 0
    sse = (
        "event: response.completed\n"
        + 'data: {"type":"response.completed","response":{"status":"completed",'
        + '"usage":{"output_tokens":1}}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_calls, response_calls
        if request.url.host == "auth.openai.com":
            refresh_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": new_token,
                    "refresh_token": "new-refresh-token",
                },
            )

        response_calls += 1
        if response_calls == 1:
            assert request.headers["authorization"] == f"Bearer {old_token}"
            return httpx.Response(403, json={"detail": "token was revoked"})

        assert request.headers["authorization"] == f"Bearer {new_token}"
        return httpx.Response(200, content=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        backend = CodexResponsesBackend(
            CodexResponsesClient(
                auth_manager=CodexAuthManager(
                    auth_file=auth_file,
                    http_client=http_client,
                ),
                http_client=http_client,
            )
        )
        chunks = [
            chunk
            async for chunk in backend.stream(
                model="gpt-5.5",
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=64,
            )
        ]

    assert chunks[-1].is_done is True
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].output_tokens == 1
    assert response_calls == 2
    assert refresh_calls == 1
