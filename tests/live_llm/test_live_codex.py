from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.config import ConfiguredModelSettings, settings
from src.llm.api import honcho_llm_call

pytestmark = [pytest.mark.live_llm]


def _require_codex_auth() -> None:
    auth_file = Path(settings.LLM.CODEX_AUTH_FILE).expanduser()
    if not auth_file.exists():
        pytest.skip(f"Missing Codex auth file: {auth_file}")


def _codex_live_tool() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_codex_live_value",
            "description": "Return the fixed live-smoke value for Codex transport tests.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Short reason the value is needed.",
                    }
                },
                "required": ["label"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_live_codex_required_tool_loop() -> None:
    _require_codex_auth()
    tool_requests: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
        tool_requests.append((name, tool_input))
        assert name == "get_codex_live_value"
        return "CODEX-LIVE-TOOL-VALUE-731"

    response = await honcho_llm_call(
        model_config=ConfiguredModelSettings(transport="codex", model="gpt-5.5"),
        prompt=(
            "Call get_codex_live_value exactly once, then answer with the exact "
            "tool value and no private details."
        ),
        max_tokens=512,
        tools=_codex_live_tool(),
        tool_executor=execute_tool,
        tool_choice="required",
        max_tool_iterations=3,
        enable_retry=False,
    )

    assert tool_requests
    assert tool_requests[0][0] == "get_codex_live_value"
    assert "CODEX-LIVE-TOOL-VALUE-731" in response.content
    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert response.iterations >= 2
