from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.config import ModelConfig
from src.llm import tool_loop
from src.llm.backends.codex import CodexResponsesClient
from src.llm.codex_auth import CodexAuthManager
from src.llm.runtime import AttemptPlan
from src.llm.tool_loop import execute_tool_loop
from src.llm.types import HonchoLLMCallResponse


def _make_plan() -> AttemptPlan:
    return AttemptPlan(
        provider="codex",
        model="gpt-5.5",
        client=CodexResponsesClient(
            auth_manager=CodexAuthManager(auth_file="/tmp/nonexistent-codex-auth.json")
        ),
        thinking_budget_tokens=None,
        reasoning_effort=None,
        selected_config=ModelConfig(model="gpt-5.5", transport="codex"),
        attempt=1,
        retry_attempts=1,
        is_fallback=False,
    )


@pytest.mark.asyncio
async def test_codex_tool_loop_uses_honcho_executor_and_tool_result_history() -> None:
    inner_calls: list[dict[str, Any]] = []
    tool_calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_inner(*_args: Any, **kwargs: Any) -> HonchoLLMCallResponse[Any]:
        inner_calls.append(kwargs)
        if len(inner_calls) == 1:
            return HonchoLLMCallResponse(
                content="",
                input_tokens=20,
                output_tokens=5,
                finish_reasons=["tool_calls"],
                tool_calls_made=[
                    {
                        "id": "call_lookup|fc_lookup",
                        "name": "lookup",
                        "input": {"query": "codex"},
                    }
                ],
            )

        messages = kwargs["messages"]
        assert messages[-2]["role"] == "assistant"
        assert messages[-2]["tool_calls"][0]["id"] == "call_lookup|fc_lookup"
        assert messages[-1] == {
            "role": "tool",
            "tool_call_id": "call_lookup|fc_lookup",
            "content": "tool-result",
        }
        return HonchoLLMCallResponse(
            content="final answer used tool-result",
            input_tokens=30,
            output_tokens=7,
            finish_reasons=["stop"],
            tool_calls_made=[],
        )

    async def tool_executor(name: str, tool_input: dict[str, Any]) -> str:
        tool_calls.append((name, tool_input))
        return "tool-result"

    with patch.object(tool_loop, "honcho_llm_call_inner", new=fake_inner):
        result = await execute_tool_loop(
            prompt="Use the lookup tool, then answer.",
            max_tokens=128,
            messages=None,
            tools=[
                {
                    "name": "lookup",
                    "description": "Lookup a test value",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                }
            ],
            tool_choice="required",
            tool_executor=tool_executor,
            max_tool_iterations=3,
            response_model=None,
            json_mode=False,
            temperature=None,
            stop_seqs=None,
            verbosity=None,
            enable_retry=False,
            retry_attempts=1,
            max_input_tokens=None,
            get_attempt_plan=_make_plan,
            before_retry_callback=lambda _state: None,
        )

    assert isinstance(result, HonchoLLMCallResponse)
    assert result.content == "final answer used tool-result"
    assert result.input_tokens == 50
    assert result.output_tokens == 12
    assert result.iterations == 2
    assert tool_calls == [("lookup", {"query": "codex"})]
    assert inner_calls[0]["tool_choice"] == "required"
    assert inner_calls[1]["tool_choice"] == "auto"
