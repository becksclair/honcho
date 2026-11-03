"""Conclusion validation follows the embedding tokenizer without provider access."""

import os
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.config import ConfiguredEmbeddingModelSettings, ModelOverrideSettings, settings
from src.embedding_client import embedding_client
from src.schemas import ConclusionBatchCreate, ConclusionCreate


@pytest.fixture
def tokenizer_path() -> str:
    path = os.environ.get("TEST_BGE_M3_TOKENIZER_PATH")
    if not path:
        pytest.skip(
            "Set TEST_BGE_M3_TOKENIZER_PATH to the pinned offline tokenizer.json"
        )
    return path


def configure_embedding(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: str = "bge-m3",
    transport: str = "openai",
    tokenizer_path: str | None = None,
    input_limit: int = 8192,
    request_limit: int = 300_000,
) -> None:
    config = ConfiguredEmbeddingModelSettings.model_validate(
        {
            "model": model,
            "transport": transport,
            "overrides": ModelOverrideSettings(api_key=""),
        }
    )
    monkeypatch.setattr(
        settings,
        "EMBEDDING",
        settings.EMBEDDING.model_copy(
            update={
                "MODEL_CONFIG": config,
                "TOKENIZER_PATH": tokenizer_path,
                "MAX_INPUT_TOKENS": input_limit,
                "MAX_TOKENS_PER_REQUEST": request_limit,
            }
        ),
    )
    monkeypatch.setattr(embedding_client, "_instance", None)
    monkeypatch.setattr(embedding_client, "_instance_signature", None)


def conclusion(content: str) -> ConclusionCreate:
    return ConclusionCreate(
        content=content, observer_id="observer", observed_id="observed"
    )


def test_bge_conclusion_accepts_literal_special_tokens_after_sanitizing(
    monkeypatch: pytest.MonkeyPatch, tokenizer_path: str
) -> None:
    configure_embedding(monkeypatch, tokenizer_path=tokenizer_path)
    result = conclusion("literal \x00<|endoftext|> 你好")
    assert result.content == "literal <|endoftext|> 你好"
    assert result._token_count == embedding_client.count_tokens(result.content)  # pyright: ignore[reportPrivateUsage]
    assert "_token_count" not in result.model_dump()


@pytest.mark.parametrize(
    ("input_limit", "request_limit", "effective_limit"),
    [(100_000, 300_000, 8192), (8, 300_000, 8), (16, 8, 8)],
)
def test_bge_conclusion_rejects_over_effective_limit_individually_and_in_batch(
    monkeypatch: pytest.MonkeyPatch,
    tokenizer_path: str,
    input_limit: int,
    request_limit: int,
    effective_limit: int,
) -> None:
    configure_embedding(
        monkeypatch,
        tokenizer_path=tokenizer_path,
        input_limit=input_limit,
        request_limit=request_limit,
    )
    at_limit = "a " * (effective_limit - 3)
    over_limit = "a " * (effective_limit - 2)
    assert conclusion(at_limit)._token_count == effective_limit  # pyright: ignore[reportPrivateUsage]
    message = rf"limit of {effective_limit} \(got {effective_limit + 1} tokens\)"
    with pytest.raises(ValidationError, match=message):
        conclusion(over_limit)
    with pytest.raises(ValidationError, match=message) as error:
        ConclusionBatchCreate.model_validate(
            {
                "conclusions": [
                    {
                        "content": text,
                        "observer_id": "observer",
                        "observed_id": "observed",
                    }
                    for text in [at_limit, over_limit]
                ]
            }
        )
    assert error.value.errors()[0]["loc"] == ("conclusions", 1)


def test_batch_keeps_individual_limits_and_legacy_alias(
    monkeypatch: pytest.MonkeyPatch, tokenizer_path: str
) -> None:
    configure_embedding(monkeypatch, tokenizer_path=tokenizer_path, request_limit=16)
    batch = ConclusionBatchCreate.model_validate(
        {"observations": [conclusion("a " * 7).model_dump()] * 2}
    )
    assert len(batch.conclusions) == 2
    assert sum(item._token_count for item in batch.conclusions) > 16  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("transport", ["openai", "gemini"])
async def test_validation_counts_without_credentials_but_embedding_still_requires_them(
    monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    configure_embedding(
        monkeypatch, model="unknown-model", transport=transport, input_limit=1
    )
    provider = Mock(side_effect=AssertionError("Provider must not initialize"))
    monkeypatch.setattr("src.embedding_client.AsyncOpenAI", provider)
    monkeypatch.setattr("src.embedding_client.genai.Client", provider)
    assert conclusion("hello")._token_count == 1  # pyright: ignore[reportPrivateUsage]
    # cl100k_base counts this as two tokens; the former o200k validator counted one.
    with pytest.raises(ValidationError, match="got 2 tokens"):
        conclusion("你好")
    provider.assert_not_called()
    with pytest.raises(ValueError, match="API key is required"):
        await embedding_client.embed("hello")
    provider.assert_not_called()
