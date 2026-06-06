# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, NoReturn, cast

import httpx
from pydantic import BaseModel

from src.config import settings
from src.exceptions import ValidationException
from src.llm.backend import CompletionResult, StreamChunk, ToolCallResult
from src.llm.codex_auth import CodexAuthManager
from src.llm.structured_output import (
    repair_response_model_json,
    validate_structured_output,
)

logger = logging.getLogger(__name__)
_AUTH_MAX_ATTEMPTS = 2
_AUTH_RETRY_STATUS_CODES = {401, 403}


class _CodexAuthRetry(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(str(status_code))
        self.status_code: int = status_code


def codex_responses_url(base_url: str) -> str:
    normalized = normalize_codex_base_url(base_url)
    return f"{normalized}/responses"


def normalize_codex_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/responses"):
        return normalized[: -len("/responses")]
    return normalized


@dataclass(slots=True)
class CodexResponsesClient:
    auth_manager: CodexAuthManager
    base_url: str = "https://chatgpt.com/backend-api/codex"
    originator: str = "honcho"
    user_agent: str = "honcho"
    timeout_seconds: float = 600.0
    max_concurrent_calls: int = 1
    http_client: httpx.AsyncClient | None = None
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)
    _owned_http_client: httpx.AsyncClient | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.max_concurrent_calls)

    async def create_response(self, body: dict[str, Any]) -> dict[str, Any]:
        client = self._get_http_client()
        try:
            for attempt in range(_AUTH_MAX_ATTEMPTS):
                try:
                    async with self._semaphore:
                        headers = await self._headers()
                        if body.get("stream") is True:
                            return await self._stream_response(client, body, headers)
                        response = await client.post(
                            codex_responses_url(self.base_url),
                            json=body,
                            headers=headers,
                        )
                        return self._decode_json_response(response)
                except _CodexAuthRetry as exc:
                    await self._handle_auth_retry(exc, attempt)
                    continue
        except httpx.HTTPError as exc:
            raise ValidationException("Codex Responses request failed") from exc
        raise ValidationException("Codex Responses request failed")

    async def stream_response(self, body: dict[str, Any]) -> AsyncIterator[StreamChunk]:
        client = self._get_http_client()
        completed_response: dict[str, Any] | None = None
        saw_tool_call = False
        try:
            for attempt in range(_AUTH_MAX_ATTEMPTS):
                completed_response = None
                saw_tool_call = False
                try:
                    async with self._semaphore:
                        headers = await self._headers()
                        stream_headers = {**headers, "Accept": "text/event-stream"}
                        async with client.stream(
                            "POST",
                            codex_responses_url(self.base_url),
                            json=body,
                            headers=stream_headers,
                        ) as response:
                            if response.status_code >= 400:
                                self._raise_response_error_or_retry(
                                    response.status_code
                                )
                            async for line in response.aiter_lines():
                                event = _decode_sse_data_line(line)
                                if event is None:
                                    continue
                                event_type = event.get("type")
                                if event_type == "response.output_text.delta":
                                    delta = event.get("delta")
                                    if isinstance(delta, str) and delta:
                                        yield StreamChunk(content=delta)
                                elif event_type == "response.output_item.done":
                                    item = event.get("item")
                                    if isinstance(item, dict) and item.get("type") == (
                                        "function_call"
                                    ):
                                        saw_tool_call = True
                                elif event_type == "response.completed":
                                    response_payload = event.get("response")
                                    if isinstance(response_payload, dict):
                                        completed_response = cast(
                                            dict[str, Any],
                                            response_payload,
                                        )
                                elif event_type == "response.failed":
                                    _raise_sse_response_status(
                                        event,
                                        fallback_status="failed",
                                    )
                                elif event_type == "response.incomplete":
                                    _raise_sse_response_status(
                                        event,
                                        fallback_status="incomplete",
                                    )
                except _CodexAuthRetry as exc:
                    await self._handle_auth_retry(exc, attempt)
                    continue
                break
        except httpx.HTTPError as exc:
            raise ValidationException("Codex Responses request failed") from exc

        if completed_response is None:
            raise ValidationException(
                "Codex Responses stream ended without completion event"
            )
        finish_reason = _finish_reason(completed_response, saw_tool_call)
        output_tokens = _usage_output_tokens(completed_response)
        yield StreamChunk(
            is_done=True,
            finish_reason=finish_reason,
            output_tokens=output_tokens,
        )

    async def aclose(self) -> None:
        if self._owned_http_client is not None:
            await self._owned_http_client.aclose()
            self._owned_http_client = None

    async def _headers(self) -> dict[str, str]:
        tokens = await self.auth_manager.get_tokens()
        return {
            "Authorization": f"Bearer {tokens.access_token}",
            "chatgpt-account-id": tokens.account_id,
            "originator": self.originator,
            "User-Agent": self.user_agent,
            "OpenAI-Beta": "responses=experimental",
        }

    def _get_http_client(self) -> httpx.AsyncClient:
        if self.http_client is not None:
            return self.http_client
        if self._owned_http_client is None:
            self._owned_http_client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._owned_http_client

    async def _handle_auth_retry(self, exc: _CodexAuthRetry, attempt: int) -> None:
        if attempt < _AUTH_MAX_ATTEMPTS - 1:
            await self.auth_manager.force_refresh()
            return
        self._raise_response_error(exc.status_code)

    async def _stream_response(
        self,
        client: httpx.AsyncClient,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        stream_headers = {**headers, "Accept": "text/event-stream"}
        completed_response: dict[str, Any] | None = None
        text_parts: list[str] = []
        output_items: list[dict[str, Any]] = []

        async with client.stream(
            "POST",
            codex_responses_url(self.base_url),
            json=body,
            headers=stream_headers,
        ) as response:
            if response.status_code >= 400:
                self._raise_response_error_or_retry(response.status_code)
            async for line in response.aiter_lines():
                event = _decode_sse_data_line(line)
                if event is None:
                    continue
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        text_parts.append(delta)
                elif event_type == "response.output_item.done":
                    item = event.get("item")
                    if isinstance(item, dict):
                        output_items.append(cast(dict[str, Any], item))
                elif event_type == "response.completed":
                    response_payload = event.get("response")
                    if isinstance(response_payload, dict):
                        completed_response = cast(dict[str, Any], response_payload)
                elif event_type == "response.failed":
                    _raise_sse_response_status(event, fallback_status="failed")
                elif event_type == "response.incomplete":
                    _raise_sse_response_status(event, fallback_status="incomplete")

        if completed_response is not None:
            if output_items:
                completed_response = {
                    **completed_response,
                    "output": _merge_output_items(
                        completed_response.get("output"),
                        output_items,
                    ),
                }
            if not completed_response.get("output_text") and text_parts:
                completed_response = {
                    **completed_response,
                    "output_text": "".join(text_parts),
                }
            return completed_response
        raise ValidationException(
            "Codex Responses stream ended without completion event"
        )

    @staticmethod
    def _decode_json_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            CodexResponsesClient._raise_response_error_or_retry(response.status_code)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ValidationException("Codex Responses returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValidationException("Codex Responses returned invalid payload")
        return cast(dict[str, Any], payload)

    @staticmethod
    def _raise_response_error(status_code: int) -> NoReturn:
        raise ValidationException(
            f"Codex Responses request failed with HTTP {status_code}"
        )

    @staticmethod
    def _raise_response_error_or_retry(status_code: int) -> NoReturn:
        if status_code in _AUTH_RETRY_STATUS_CODES:
            raise _CodexAuthRetry(status_code)
        CodexResponsesClient._raise_response_error(status_code)


def make_default_codex_client(base_url: str | None = None) -> CodexResponsesClient:
    return CodexResponsesClient(
        auth_manager=CodexAuthManager(
            auth_file=settings.LLM.CODEX_AUTH_FILE,
            refresh_skew_seconds=settings.LLM.CODEX_REFRESH_SKEW_SECONDS,
        ),
        base_url=normalize_codex_base_url(base_url or settings.LLM.CODEX_BASE_URL),
        originator=settings.LLM.CODEX_ORIGINATOR,
        user_agent=settings.LLM.CODEX_USER_AGENT,
        timeout_seconds=settings.LLM.CODEX_TIMEOUT_SECONDS,
        max_concurrent_calls=settings.LLM.CODEX_MAX_CONCURRENT_CALLS,
    )


class CodexResponsesBackend:
    """Provider backend for ChatGPT Codex Responses."""

    def __init__(self, client: CodexResponsesClient) -> None:
        self._client: CodexResponsesClient = client

    @staticmethod
    def _build_request_body(
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float | None = None,
        stop: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: type[BaseModel] | dict[str, Any] | None = None,
        thinking_budget_tokens: int | None = None,
        thinking_effort: str | None = None,
        max_output_tokens: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if stop:
            raise ValidationException("codex transport does not support stop sequences")
        if thinking_budget_tokens is not None:
            raise ValidationException(
                "codex transport uses thinking_effort, not thinking_budget_tokens"
            )
        del max_tokens, max_output_tokens
        return build_codex_responses_request(
            model=model,
            messages=messages,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            thinking_effort=thinking_effort,
            extra_params=extra_params,
        )

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float | None = None,
        stop: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: type[BaseModel] | dict[str, Any] | None = None,
        thinking_budget_tokens: int | None = None,
        thinking_effort: str | None = None,
        max_output_tokens: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> CompletionResult:
        body = self._build_request_body(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            thinking_budget_tokens=thinking_budget_tokens,
            thinking_effort=thinking_effort,
            max_output_tokens=max_output_tokens,
            extra_params=extra_params,
        )
        response = await self._client.create_response(body)
        result = normalize_codex_response(response)
        if isinstance(response_format, type) and isinstance(result.content, str):
            parsed = repair_response_model_json(result.content, response_format, model)
            result.content = validate_structured_output(parsed, response_format)
        return result

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float | None = None,
        stop: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: type[BaseModel] | dict[str, Any] | None = None,
        thinking_budget_tokens: int | None = None,
        thinking_effort: str | None = None,
        max_output_tokens: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        body = self._build_request_body(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            thinking_budget_tokens=thinking_budget_tokens,
            thinking_effort=thinking_effort,
            max_output_tokens=max_output_tokens,
            extra_params=extra_params,
        )
        async for chunk in self._client.stream_response(body):
            yield chunk


def build_codex_responses_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    response_format: type[BaseModel] | dict[str, Any] | None = None,
    thinking_effort: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instructions, input_items = convert_messages_to_responses_input(messages)
    reasoning_effort = _reasoning_effort(model, thinking_effort)
    _validate_extra_params(extra_params)
    text: dict[str, Any] = {
        "verbosity": _verbosity(extra_params),
    }
    format_param = _convert_response_format(response_format, extra_params)
    if format_param is not None:
        text["format"] = format_param

    body: dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        "input": input_items,
        "text": text,
        "parallel_tool_calls": True,
    }
    body["instructions"] = instructions or "You are a concise assistant."
    if temperature is not None:
        body["temperature"] = temperature
    if reasoning_effort is not None:
        body["reasoning"] = {"effort": reasoning_effort}

    converted_tools = convert_tools_to_responses(tools)
    if converted_tools:
        body["tools"] = converted_tools
        body["tool_choice"] = convert_tool_choice(tool_choice)

    if extra_params:
        for key in ("service_tier", "prompt_cache_key", "include", "top_p"):
            if key in extra_params and extra_params[key] is not None:
                body[key] = extra_params[key]
    return body


def convert_messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content")
        if role in {"system", "developer"}:
            text = _message_text(content)
            if text:
                instructions.append(text)
            continue

        if role == "tool":
            call_id = _call_id_from_tool_id(str(message.get("tool_call_id", "")))
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _message_text(content),
                }
            )
            continue

        if role == "assistant":
            text = _message_text(content)
            if text:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            for tool_call in message.get("tool_calls") or []:
                converted = _convert_assistant_tool_call(tool_call)
                if converted is not None:
                    input_items.append(converted)
            continue

        input_items.append(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": _message_text(content)}],
            }
        )

    return ("\n\n".join(instructions) if instructions else None), input_items


def convert_tools_to_responses(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not tools:
        return []
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            fn = cast(dict[str, Any], tool["function"])
            name = fn.get("name")
            description = fn.get("description", "")
            parameters = fn.get("parameters") or {}
        else:
            name = tool.get("name")
            description = tool.get("description", "")
            parameters = tool.get("input_schema") or {}
        if not isinstance(name, str) or not name:
            continue
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": description if isinstance(description, str) else "",
                "parameters": parameters,
                "strict": None,
            }
        )
    return converted


def convert_tool_choice(
    tool_choice: str | dict[str, Any] | None,
) -> str | dict[str, Any]:
    if tool_choice is None:
        return "auto"
    if tool_choice == "any":
        return "required"
    if isinstance(tool_choice, str):
        if tool_choice in {"auto", "none", "required"}:
            return tool_choice
        return {"type": "function", "name": tool_choice}
    if tool_choice.get("type") == "function":
        function = tool_choice.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return {"type": "function", "name": function["name"]}
        if isinstance(tool_choice.get("name"), str):
            return {"type": "function", "name": tool_choice["name"]}
    if tool_choice.get("type") == "tool" and isinstance(tool_choice.get("name"), str):
        return {"type": "function", "name": tool_choice["name"]}
    raise ValidationException("Unsupported Codex tool_choice shape")


def normalize_codex_response(response: dict[str, Any]) -> CompletionResult:
    output = response.get("output") or []
    output_items = output if isinstance(output, list) else []
    text_parts: list[str] = []
    tool_calls: list[ToolCallResult] = []
    reasoning_details: list[dict[str, Any]] = []

    top_output_text = response.get("output_text")
    if isinstance(top_output_text, str) and top_output_text:
        text_parts.append(top_output_text)
        has_top_output_text = True
    else:
        has_top_output_text = False

    for item in output_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message" and not has_top_output_text:
            text_parts.extend(_message_output_texts(item.get("content")))
        elif item_type == "function_call":
            parsed = _tool_call_from_response_item(item)
            if parsed is not None:
                tool_calls.append(parsed)
        elif item_type == "reasoning":
            summary = item.get("summary")
            if isinstance(summary, list):
                for block in summary:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str) and text:
                            reasoning_details.append(
                                {"type": "reasoning_summary", "content": text}
                            )

    usage = response.get("usage")
    input_tokens = _int_from_mapping(usage, "input_tokens")
    output_tokens = _int_from_mapping(usage, "output_tokens")
    cache_read = 0
    if isinstance(usage, dict):
        details = usage.get("input_tokens_details")
        cache_read = _int_from_mapping(details, "cached_tokens")

    finish_reason = _finish_reason(response, bool(tool_calls))

    return CompletionResult(
        content="".join(text_parts),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        reasoning_details=reasoning_details,
        raw_response=response,
    )


def _convert_assistant_tool_call(tool_call: Any) -> dict[str, Any] | None:
    if not isinstance(tool_call, dict):
        return None
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str) or not name:
        return None
    tool_id = str(tool_call.get("id") or "")
    call_id, item_id = _split_tool_id(tool_id)
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments or {})
    item: dict[str, Any] = {
        "type": "function_call",
        "call_id": call_id or tool_id,
        "name": name,
        "arguments": arguments,
    }
    if item_id:
        item["id"] = item_id
    return item


def _tool_call_from_response_item(item: dict[str, Any]) -> ToolCallResult | None:
    name = item.get("name")
    if not isinstance(name, str) or not name:
        return None
    args: dict[str, Any] = {}
    arguments = item.get("arguments")
    if isinstance(arguments, str) and arguments:
        try:
            parsed_args = json.loads(arguments)
            if isinstance(parsed_args, dict):
                args = cast(dict[str, Any], parsed_args)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Codex tool call arguments were invalid JSON: "
                + "name=%s call_id=%s item_id=%s exception=%s",
                name,
                str(item.get("call_id") or ""),
                str(item.get("id") or ""),
                exc.__class__.__name__,
            )
            args = {}
    elif isinstance(arguments, dict):
        args = cast(dict[str, Any], arguments)

    call_id = str(item.get("call_id") or "")
    item_id = str(item.get("id") or "")
    tool_id = f"{call_id}|{item_id}" if call_id and item_id else call_id or item_id
    return ToolCallResult(id=tool_id, name=name, input=args)


def _merge_output_items(
    completed_output: Any,
    streamed_output: list[dict[str, Any]],
) -> list[Any]:
    merged = list(completed_output) if isinstance(completed_output, list) else []
    seen = {_output_item_key(item) for item in merged if isinstance(item, dict)}
    for item in streamed_output:
        key = _output_item_key(item)
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _output_item_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("type"),
        item.get("id"),
        item.get("call_id"),
        item.get("name"),
    )


def _split_tool_id(tool_id: str) -> tuple[str, str | None]:
    if "|" not in tool_id:
        return tool_id, None
    call_id, item_id = tool_id.split("|", 1)
    return call_id, item_id or None


def _call_id_from_tool_id(tool_id: str) -> str:
    call_id, _item_id = _split_tool_id(tool_id)
    return call_id


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return str(content)


def _message_output_texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        text = block.get("text")
        if block_type in {"output_text", "text"} and isinstance(text, str):
            parts.append(text)
    return parts


def _int_from_mapping(value: Any, key: str) -> int:
    if not isinstance(value, dict):
        return 0
    raw = value.get(key)
    return int(raw) if isinstance(raw, int | float) else 0


def _usage_output_tokens(response: dict[str, Any] | None) -> int | None:
    if response is None:
        return None
    usage = response.get("usage")
    output_tokens = _int_from_mapping(usage, "output_tokens")
    return output_tokens if output_tokens > 0 else None


def _finish_reason(response: dict[str, Any], has_tool_calls: bool) -> str:
    status = response.get("status")
    if status == "completed":
        return "tool_calls" if has_tool_calls else "stop"
    if isinstance(status, str):
        incomplete_details = response.get("incomplete_details")
        reason = (
            incomplete_details.get("reason")
            if isinstance(incomplete_details, dict)
            else None
        )
        if isinstance(reason, str) and reason:
            raise ValidationException(
                f"Codex Responses returned status {status!r}: {reason}"
            )
    raise ValidationException(f"Codex Responses returned status {status!r}")


def _raise_sse_response_status(
    event: dict[str, Any],
    *,
    fallback_status: str,
) -> None:
    response_payload = event.get("response")
    if isinstance(response_payload, dict):
        response = cast(dict[str, Any], response_payload)
    else:
        response: dict[str, Any] = {"status": fallback_status}
        incomplete_details = event.get("incomplete_details")
        if isinstance(incomplete_details, dict):
            response["incomplete_details"] = incomplete_details
    _finish_reason(response, has_tool_calls=False)
    raise ValidationException(f"Codex Responses returned status {fallback_status!r}")


def _decode_sse_data_line(line: str) -> dict[str, Any] | None:
    if not line or not line.startswith("data:"):
        return None
    return _decode_sse_data(line[5:].strip())


def _decode_sse_data(data: str) -> dict[str, Any] | None:
    if data == "[DONE]":
        return None
    try:
        event = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValidationException("Codex Responses stream returned invalid JSON") from exc
    return cast(dict[str, Any], event) if isinstance(event, dict) else None


def _verbosity(extra_params: dict[str, Any] | None) -> str:
    if not extra_params:
        return "low"
    raw = extra_params.get("verbosity")
    return raw if raw in {"low", "medium", "high"} else "low"


def _validate_extra_params(extra_params: dict[str, Any] | None) -> None:
    if not extra_params:
        return
    allowed = {
        "include",
        "json_mode",
        "prompt_cache_key",
        "service_tier",
        "top_p",
        "verbosity",
    }
    unsupported = [
        key for key, value in extra_params.items() if value is not None and key not in allowed
    ]
    if unsupported:
        raise ValidationException(
            "codex transport does not support provider params: "
            + ", ".join(sorted(unsupported))
        )


def _reasoning_effort(model: str, thinking_effort: str | None) -> str | None:
    if not thinking_effort or thinking_effort == "none":
        return None
    if model == "gpt-5.5" and thinking_effort in {"minimal", "low"}:
        raise ValidationException(
            "gpt-5.5 must not use minimal or low reasoning_effort; "
            + "use medium or higher"
        )
    return thinking_effort


def _convert_response_format(
    response_format: type[BaseModel] | dict[str, Any] | None,
    extra_params: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if isinstance(response_format, type):
        return {
            "type": "json_schema",
            "name": response_format.__name__,
            "schema": response_format.model_json_schema(),
            "strict": False,
        }
    if isinstance(response_format, dict):
        if response_format.get("type") == "json_object":
            return {"type": "json_object"}
        if response_format.get("type") == "json_schema":
            return response_format
    if extra_params and extra_params.get("json_mode"):
        return {"type": "json_object"}
    return None


__all__ = [
    "CodexResponsesBackend",
    "CodexResponsesClient",
    "build_codex_responses_request",
    "codex_responses_url",
    "convert_messages_to_responses_input",
    "convert_tool_choice",
    "convert_tools_to_responses",
    "make_default_codex_client",
    "normalize_codex_response",
    "normalize_codex_base_url",
]
