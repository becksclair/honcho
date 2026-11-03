"""BGE contracts use the pinned offline artifact, never a live provider."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import tiktoken
import tiktoken.load
import tiktoken.registry

from src.config import EmbeddingModelConfig
from src.embedding_client import _EmbeddingClient
from src.embedding_tokenizer import BgeM3Tokenizer, is_bge_m3


@pytest.fixture
def tokenizer_path() -> str:
    path = os.environ.get("TEST_BGE_M3_TOKENIZER_PATH")
    if not path:
        pytest.skip(
            "Set TEST_BGE_M3_TOKENIZER_PATH to the pinned offline tokenizer.json"
        )
    return path


@pytest.fixture
def tokenizer(tokenizer_path: str) -> BgeM3Tokenizer:
    return BgeM3Tokenizer(tokenizer_path)


def test_real_bge_boundary_includes_special_tokens(tokenizer: BgeM3Tokenizer) -> None:
    at_limit = "a " * 8189
    over_limit = "a " * 8190
    assert tokenizer.count(at_limit) == 8192
    assert tokenizer.count(over_limit) == 8193
    assert tokenizer.chunks(at_limit, 8192) == [(at_limit, 8192)]
    chunks = tokenizer.chunks(over_limit, 8192)
    assert len(chunks) == 2
    assert all(count == tokenizer.count(text) <= 8192 for text, count in chunks)


@pytest.mark.parametrize(
    "text",
    [
        " 你好\tCafé e\u0301 🇪🇸\n  " * 100,
        "\t\n   " * 100,
        "🙂" * 100,
        "<s> raw special token </s>\n" * 100,
    ],
    ids=["multilingual", "whitespace", "emoji", "literal-special-tokens"],
)
def test_chunks_preserve_exact_unicode_and_full_coverage(
    tokenizer: BgeM3Tokenizer, text: str
) -> None:
    spans = tokenizer.chunk_spans(text, 16)
    chunks = tokenizer.chunks(text, 16)
    previous_start = -1
    covered_end = 0
    for (start, end, count), (chunk, chunk_count) in zip(spans, chunks, strict=True):
        assert previous_start < start <= covered_end
        assert chunk == text[start:end]
        covered_end = end
        previous_start = start
        assert count == chunk_count == tokenizer.count(chunk) <= 16
    assert covered_end == len(text)
    if len(chunks) > 1:
        assert sum(len(chunk) for chunk, _ in chunks) > len(text)


def test_tokenizer_rejects_changed_artifact(tmp_path: Path) -> None:
    path = tmp_path / "tokenizer.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="SHA-256"):
        BgeM3Tokenizer(path)


@pytest.mark.parametrize(
    "model", ["bge-m3", "bge-m3:latest", "BAAI/bge-m3", "@cf/baai/bge-m3"]
)
def test_bge_aliases(model: str) -> None:
    assert is_bge_m3(model)
    assert not is_bge_m3("another-bge-m3-model")


def make_client(
    monkeypatch: pytest.MonkeyPatch,
    tokenizer_path: str | None,
    *,
    model: str = "@cf/baai/bge-m3",
    limit: int = 8192,
    request_limit: int = 300_000,
) -> tuple[_EmbeddingClient, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[float(len(text))])
                for text in kwargs["input"]
            ]
        )

    monkeypatch.setattr(
        "src.embedding_client.AsyncOpenAI",
        lambda **_: SimpleNamespace(embeddings=SimpleNamespace(create=create)),
    )
    client = _EmbeddingClient(
        EmbeddingModelConfig(transport="openai", model=model, api_key="test"),
        vector_dimensions=1,
        max_input_tokens=limit,
        max_tokens_per_request=request_limit,
        send_dimensions=False,
        tokenizer_path=tokenizer_path,
    )
    return client, calls


async def test_bge_embedding_works_with_cold_offline_lexical_cache(
    monkeypatch: pytest.MonkeyPatch, tokenizer_path: str, tmp_path: Path
) -> None:
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})
    download = Mock(side_effect=OSError("Network unavailable"))
    monkeypatch.setattr(tiktoken.load, "read_file", download)

    client, calls = make_client(monkeypatch, tokenizer_path, limit=16)
    text = "你好 café " * 10
    assert client.count_tokens(text) > 16
    chunks = client.prepare_chunks({"parent": text})["parent"]
    assert len(chunks) > 1
    await client.embed(chunks[0])
    await client.simple_batch_embed(chunks)
    await client.batch_embed({"parent": text})
    assert calls
    download.assert_not_called()


def test_lexical_encoding_loads_on_first_access_and_stays_cached(
    monkeypatch: pytest.MonkeyPatch, tokenizer_path: str
) -> None:
    load = Mock(wraps=tiktoken.encoding_for_model)
    monkeypatch.setattr(tiktoken, "encoding_for_model", load)
    client, _ = make_client(monkeypatch, tokenizer_path)
    load.assert_not_called()
    encoding = client.encoding
    assert encoding.name == "cl100k_base"
    assert client.encoding is encoding
    load.assert_called_once_with(client.model)


async def test_query_and_unchunked_documents_reject_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tokenizer_path: str
) -> None:
    client, calls = make_client(monkeypatch, tokenizer_path, limit=100_000)
    assert client.max_embedding_tokens == 8192
    with pytest.raises(ValueError, match="8193 tokens"):
        await client.embed("a " * 8190)
    with pytest.raises(ValueError, match="index 1"):
        await client.simple_batch_embed(["valid", "a " * 8190])
    assert calls == []
    assert await client.embed("a " * 8189) == [float(len("a " * 8189))]
    assert len(calls) == 1


async def test_persisted_chunks_reembed_in_order_and_charge_real_batch_tokens(
    monkeypatch: pytest.MonkeyPatch, tokenizer_path: str
) -> None:
    client, calls = make_client(monkeypatch, tokenizer_path, limit=16, request_limit=40)
    source = " 你好\tCafé e\u0301 🇪🇸\n  " * 30
    chunks = client.prepare_chunks({"parent": source})["parent"]
    assert client.encoding.name == "cl100k_base"  # lexical dedup remains separate
    assert all(client.count_tokens(chunk) <= 16 for chunk in chunks)
    # Reconciliation receives separately persisted sibling rows in ordinal order.
    rows = {str(i): chunk for i, chunk in enumerate(chunks)}
    result = await client.batch_embed(rows)
    assert [text for call in calls for text in call["input"]] == chunks
    assert all(
        sum(client.count_tokens(text) for text in call["input"]) <= 40 for call in calls
    )
    assert list(result) == list(rows)
    assert [result[str(i)] for i in range(len(chunks))] == [
        [[float(len(chunk))]] for chunk in chunks
    ]


async def test_other_models_keep_existing_tokenizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = make_client(monkeypatch, None, model="unknown-model")
    text = "你好 café"
    assert client.count_tokens(text) == len(
        tiktoken.get_encoding("cl100k_base").encode(text)
    )
    assert client.prepare_chunks({"id": text}) == {"id": [text]}
    await client.simple_batch_embed([text])
    assert calls[0]["input"] == [text]


def test_bge_requires_explicit_offline_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="TOKENIZER_PATH"):
        make_client(monkeypatch, None)


async def test_bge_request_cap_limits_queries_documents_and_persisted_chunks(
    monkeypatch: pytest.MonkeyPatch, tokenizer_path: str
) -> None:
    client, calls = make_client(monkeypatch, tokenizer_path, limit=16, request_limit=8)
    text = "a " * 7
    assert client.count_tokens(text) == 10
    assert client.max_embedding_tokens == 8
    with pytest.raises(ValueError, match="maximum token limit of 8"):
        await client.embed(text)
    with pytest.raises(ValueError, match="maximum token limit of 8"):
        await client.simple_batch_embed(["valid", text])
    assert calls == []

    chunks = client.prepare_chunks({"parent": text})["parent"]
    assert len(chunks) > 1
    assert all(client.count_tokens(chunk) <= 8 for chunk in chunks)
    batches = client._create_batches(client._prepare_chunks({"parent": text}))
    assert all(sum(item.token_count for item in batch) <= 8 for batch in batches)
    result = await client.batch_embed({"parent": text})
    assert [part for call in calls for part in call["input"]] == chunks
    assert all(
        sum(client.count_tokens(part) for part in call["input"]) <= 8 for call in calls
    )
    assert result == {"parent": [[float(len(chunk))] for chunk in chunks]}
