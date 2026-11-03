"""Persisted oversized inputs must not consume valid siblings' retries."""

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import models
from src.config import EmbeddingModelConfig, settings
from src.embedding_client import _EmbeddingClient  # pyright: ignore[reportPrivateUsage]
from src.reconciler.sync_vectors import (
    MAX_SYNC_ATTEMPTS,
    _embed_pending_inputs,  # pyright: ignore[reportPrivateUsage]
    _sync_documents,  # pyright: ignore[reportPrivateUsage]
    _sync_message_embeddings,  # pyright: ignore[reportPrivateUsage]
)
from src.vector_store import VectorStore


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["document", "message-external", "message-pgvector"])
@pytest.mark.parametrize("all_oversized", [False, True])
async def test_oversized_pending_inputs_do_not_fail_fitting_siblings(
    db_session: AsyncSession,
    sample_data: tuple[models.Workspace, models.Peer],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    all_oversized: bool,
) -> None:
    tokenizer_path = os.environ.get("TEST_BGE_M3_TOKENIZER_PATH")
    if not tokenizer_path:
        pytest.skip("Set TEST_BGE_M3_TOKENIZER_PATH to the pinned offline artifact")

    dimensions = settings.EMBEDDING.VECTOR_DIMENSIONS

    def response(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[float(index + 1)] * dimensions)
                for index, _ in enumerate(kwargs["input"])
            ]
        )

    request = AsyncMock(side_effect=response)

    def client_factory(**_: Any) -> SimpleNamespace:
        return SimpleNamespace(embeddings=SimpleNamespace(create=request))

    monkeypatch.setattr(
        "src.embedding_client.AsyncOpenAI",
        client_factory,
    )
    client = _EmbeddingClient(
        EmbeddingModelConfig(transport="openai", model="bge-m3", api_key="test"),
        vector_dimensions=dimensions,
        max_input_tokens=8192,
        max_tokens_per_request=300_000,
        send_dimensions=False,
        tokenizer_path=tokenizer_path,
    )
    monkeypatch.setattr("src.reconciler.sync_vectors.embedding_client", client)
    oversized = "a " * 8190
    assert client.count_tokens(oversized) == 8193
    contents = [oversized] * 3 if all_oversized else ["first", oversized, "last"]
    workspace, peer = sample_data
    session = models.Session(workspace_name=workspace.name, name="pending-chunks")
    db_session.add(session)
    await db_session.flush()

    documents: list[models.Document] = []
    embeddings: list[models.MessageEmbedding] = []
    if kind == "document":
        db_session.add(
            models.Collection(
                workspace_name=workspace.name, observer=peer.name, observed=peer.name
            )
        )
        await db_session.flush()
        documents = [
            models.Document(
                workspace_name=workspace.name,
                observer=peer.name,
                observed=peer.name,
                session_name=session.name,
                content=content,
                embedding=None,
                sync_state="pending",
                sync_attempts=MAX_SYNC_ATTEMPTS - 1,
            )
            for content in contents
        ]
    else:
        message = models.Message(
            workspace_name=workspace.name,
            session_name=session.name,
            peer_name=peer.name,
            content="Original message retains its chunk IDs",
            seq_in_session=1,
        )
        db_session.add(message)
        await db_session.flush()
        embeddings = [
            models.MessageEmbedding(
                workspace_name=workspace.name,
                peer_name=peer.name,
                session_name=session.name,
                message_id=message.public_id,
                content=content,
                embedding=None,
                sync_state="pending",
                sync_attempts=MAX_SYNC_ATTEMPTS - 1,
            )
            for content in contents
        ]
    rows: list[models.Document | models.MessageEmbedding] = [*documents, *embeddings]
    db_session.add_all(rows)
    await db_session.commit()
    store = MagicMock(spec=VectorStore)
    store.get_vector_namespace.return_value = "test-namespace"
    store.upsert_many = AsyncMock()
    if kind == "document":
        result = await _sync_documents(db_session, documents, store)
    else:
        result = await _sync_message_embeddings(
            db_session, embeddings, None if kind == "message-pgvector" else store
        )
    await db_session.commit()

    assert result == ((0, 3) if all_oversized else (2, 1))
    for row in rows:
        await db_session.refresh(row)
        if row.content == oversized:
            assert row.sync_state == "failed"
            assert row.sync_attempts == MAX_SYNC_ATTEMPTS
            assert row.embedding is None
        else:
            assert row.sync_state == "synced"
            assert row.sync_attempts == 0
            assert row.embedding is not None

    if all_oversized:
        request.assert_not_awaited()
        store.upsert_many.assert_not_awaited()
    else:
        request.assert_awaited_once()
        assert request.await_args
        assert request.await_args.kwargs["input"] == ["first", "last"]
        if kind == "message-external":
            records = store.upsert_many.await_args.args[1]
            assert [record.id for record in records] == [
                f"{embeddings[0].message_id}_0",
                f"{embeddings[2].message_id}_2",
            ]


@pytest.mark.asyncio
async def test_unrelated_embedding_validation_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(
        simple_batch_embed=AsyncMock(
            side_effect=ValueError("Embedding dimension mismatch")
        ),
        count_tokens=MagicMock(return_value=1),
        max_embedding_tokens=8192,
    )
    monkeypatch.setattr("src.reconciler.sync_vectors.embedding_client", client)
    with pytest.raises(ValueError, match="dimension mismatch"):
        await _embed_pending_inputs(["fitting"])
    client.simple_batch_embed.assert_awaited_once_with(["fitting"])
