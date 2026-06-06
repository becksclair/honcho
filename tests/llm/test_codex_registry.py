from __future__ import annotations

from pathlib import Path

import pytest

from src.config import ModelConfig, settings
from src.exceptions import ValidationException
from src.llm.backends.codex import CodexResponsesBackend, CodexResponsesClient
from src.llm.codex_auth import CodexAuthManager
from src.llm.history_adapters import OpenAIHistoryAdapter
from src.llm.registry import (
    CLIENTS,
    backend_for_provider,
    client_for_model_config,
    get_codex_client,
    get_codex_override_client,
    history_adapter_for_provider,
)


def test_codex_registry_does_not_require_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.LLM, "OPENAI_API_KEY", None)

    client = client_for_model_config(
        "codex",
        ModelConfig(model="gpt-5.5", transport="codex"),
    )
    backend = backend_for_provider("codex", client)

    assert isinstance(client, CodexResponsesClient)
    assert isinstance(backend, CodexResponsesBackend)
    assert isinstance(history_adapter_for_provider("codex"), OpenAIHistoryAdapter)


def test_codex_base_url_override_uses_cached_client() -> None:
    get_codex_override_client.cache_clear()
    config = ModelConfig(
        model="gpt-5.5",
        transport="codex",
        base_url="https://example.test/codex",
    )

    first = client_for_model_config("codex", config)
    second = client_for_model_config("codex", config)

    assert first is second
    assert isinstance(first, CodexResponsesClient)
    assert first.base_url == "https://example.test/codex"


def test_codex_base_url_override_normalizes_equivalent_urls() -> None:
    get_codex_override_client.cache_clear()
    first = client_for_model_config(
        "codex",
        ModelConfig(
            model="gpt-5.5",
            transport="codex",
            base_url="https://example.test/codex",
        ),
    )
    second = client_for_model_config(
        "codex",
        ModelConfig(
            model="gpt-5.5",
            transport="codex",
            base_url="https://example.test/codex/responses/",
        ),
    )

    assert first is second
    assert isinstance(first, CodexResponsesClient)
    assert first.base_url == "https://example.test/codex"


def test_codex_default_equivalent_base_url_uses_default_client() -> None:
    default_client = get_codex_client()
    client = client_for_model_config(
        "codex",
        ModelConfig(
            model="gpt-5.5",
            transport="codex",
            base_url=settings.LLM.CODEX_BASE_URL + "/responses/",
        ),
    )

    assert client is default_client


def test_codex_default_equivalent_base_url_uses_clients_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    patched_client = CodexResponsesClient(
        auth_manager=CodexAuthManager(auth_file=tmp_path / "auth.json")
    )
    monkeypatch.setitem(CLIENTS, "codex", patched_client)

    client = client_for_model_config(
        "codex",
        ModelConfig(
            model="gpt-5.5",
            transport="codex",
            base_url=settings.LLM.CODEX_BASE_URL + "/responses/",
        ),
    )

    assert client is patched_client


def test_codex_rejects_api_key_override() -> None:
    with pytest.raises(ValidationException, match="CODEX_AUTH_FILE"):
        client_for_model_config(
            "codex",
            ModelConfig(
                model="gpt-5.5",
                transport="codex",
                api_key="ignored-key",
            ),
        )
