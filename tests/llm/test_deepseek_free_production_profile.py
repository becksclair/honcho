from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import tomllib

from src.config import ConfiguredModelSettings, SummarySettings, resolve_model_config

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = REPO_ROOT / "config-deepseek-free-primary.toml"
NIM_MODEL = "deepseek-ai/deepseek-v4-flash-0731"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
LIGHTNING_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
LIGHTNING_REASONING_BUDGET = 128


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _generation_lanes(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    levels = config["dialectic"]["levels"]
    return [
        ("deriver", config["deriver"]["model_config"]),
        ("dialectic.minimal", levels["minimal"]["model_config"]),
        ("dialectic.low", levels["low"]["model_config"]),
        ("dialectic.medium", levels["medium"]["model_config"]),
        ("dialectic.high", levels["high"]["model_config"]),
        ("dialectic.max", levels["max"]["model_config"]),
        ("summary.long", config["summary"]["model_config"]),
        ("dream.deduction", config["dream"]["deduction_model_config"]),
        ("dream.induction", config["dream"]["induction_model_config"]),
    ]


def test_deepseek_free_profile_routes_non_short_generation_lanes_to_nim_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _load(PROFILE)
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "zen-test-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nim-test-key")

    lanes = _generation_lanes(config)
    assert len(lanes) == 9

    for name, raw in lanes:
        assert raw["model"] == "deepseek-v4-flash-free", name
        assert raw["transport"] == "openai", name

        fallback = raw["fallback"]
        assert fallback["model"] == NIM_MODEL, name
        assert fallback["transport"] == "openai", name
        assert fallback["thinking_effort"] == "high", name
        assert fallback["overrides"] == {
            "base_url": NIM_BASE_URL,
            "api_key_env": "NVIDIA_API_KEY",
        }, name

        if name == "deriver":
            assert fallback["structured_output_mode"] == "json_object"
        else:
            assert "structured_output_mode" not in fallback

        resolved = resolve_model_config(ConfiguredModelSettings.model_validate(raw))
        assert resolved.fallback is not None, name
        assert resolved.fallback.model == NIM_MODEL, name
        assert resolved.fallback.base_url == NIM_BASE_URL, name
        assert resolved.fallback.api_key == "nim-test-key", name
        assert resolved.fallback.thinking_effort == "high", name


def test_deepseek_free_profile_routes_short_summary_to_lightning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _load(PROFILE)
    active = _load(REPO_ROOT / "config.toml")
    monkeypatch.setenv("NVIDIA_API_KEY", "nim-test-key")

    raw = config["summary"]["short_model_config"]
    summary_settings = SummarySettings.model_validate(
        {
            "MODEL_CONFIG": config["summary"]["model_config"],
            "SHORT_MODEL_CONFIG": raw,
        }
    )
    assert summary_settings.SHORT_MODEL_CONFIG is not None
    assert summary_settings.SHORT_MODEL_CONFIG.model == LIGHTNING_MODEL
    assert summary_settings.MODEL_CONFIG.model == "deepseek-v4-flash-free"
    assert active["summary"]["short_model_config"] == raw
    assert raw["transport"] == "openai"
    assert raw["model"] == LIGHTNING_MODEL
    assert "thinking_effort" not in raw
    assert raw["overrides"] == {
        "base_url": NIM_BASE_URL,
        "api_key_env": "NVIDIA_API_KEY",
        "provider_params": {
            "extra_body": {
                "reasoning_budget": LIGHTNING_REASONING_BUDGET,
                "chat_template_kwargs": {"enable_thinking": True},
            }
        },
    }

    fallback = raw["fallback"]
    assert fallback["model"] == NIM_MODEL
    assert fallback["transport"] == "openai"
    assert fallback["thinking_effort"] == "high"
    assert fallback["overrides"] == {
        "base_url": NIM_BASE_URL,
        "api_key_env": "NVIDIA_API_KEY",
    }

    resolved = resolve_model_config(ConfiguredModelSettings.model_validate(raw))
    assert resolved.model == LIGHTNING_MODEL
    assert resolved.base_url == NIM_BASE_URL
    assert resolved.api_key == "nim-test-key"
    assert resolved.thinking_effort is None
    assert resolved.provider_params == {
        "extra_body": {
            "reasoning_budget": LIGHTNING_REASONING_BUDGET,
            "chat_template_kwargs": {"enable_thinking": True},
        }
    }
    assert resolved.fallback is not None
    assert resolved.fallback.model == NIM_MODEL
    assert resolved.fallback.thinking_effort == "high"


def test_nim_fallback_is_scoped_to_deepseek_free_profile() -> None:
    for name in ("config-codex-primary.toml", "config-deepseek-primary.toml"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert NIM_MODEL not in text, name
        assert LIGHTNING_MODEL not in text, name
        assert "NVIDIA_API_KEY" not in text, name
