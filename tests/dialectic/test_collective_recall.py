"""End-to-end proofs for workspace-scoped Dialectic recall."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import crud, models, schemas
from src.config import settings
from src.dialectic.chat import resolve_recall_observers
from src.dialectic.retrieval import MemoryEvidenceSet
from src.exceptions import ValidationException
from src.utils.agent_tools import get_observation_context
from src.utils.config_helpers import get_configuration


def _vector(*values: float) -> list[float]:
    dimensions = settings.EMBEDDING.VECTOR_DIMENSIONS
    return [*values, *([0.0] * (dimensions - len(values)))]


async def _add_peer(db: AsyncSession, workspace_name: str, name: str) -> models.Peer:
    peer = models.Peer(name=name, workspace_name=workspace_name)
    db.add(peer)
    await db.flush()
    return peer


async def _add_session(
    db: AsyncSession,
    workspace_name: str,
    name: str,
    peer_names: list[str],
) -> models.Session:
    session = models.Session(name=name, workspace_name=workspace_name)
    db.add(session)
    await db.flush()
    await crud.set_peers_for_session(
        db,
        workspace_name=workspace_name,
        session_name=name,
        peer_names={peer_name: schemas.SessionPeerConfig() for peer_name in peer_names},
    )
    return session


async def _add_collection(
    db: AsyncSession,
    workspace_name: str,
    observer: str,
    observed: str,
) -> None:
    db.add(
        models.Collection(
            workspace_name=workspace_name,
            observer=observer,
            observed=observed,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_workspace_recall_globally_ranks_observer_collections(
    db_session: AsyncSession,
    sample_data: tuple[models.Workspace, models.Peer],
) -> None:
    workspace, _ = sample_data
    bex = await _add_peer(db_session, workspace.name, "bex")
    luke = await _add_peer(db_session, workspace.name, "luke")
    sarah = await _add_peer(db_session, workspace.name, "sarah")
    sky = await _add_peer(db_session, workspace.name, "sky")
    kate = await _add_peer(db_session, workspace.name, "kate")
    session = await _add_session(
        db_session,
        workspace.name,
        "shared-recall",
        [bex.name, luke.name, sarah.name, sky.name, kate.name],
    )

    for observer in (luke.name, sarah.name, sky.name, kate.name):
        await _add_collection(db_session, workspace.name, observer, bex.name)

    now = datetime.now(timezone.utc)
    docs = [
        models.Document(
            workspace_name=workspace.name,
            observer=sarah.name,
            observed=bex.name,
            session_name=session.name,
            content="Sarah knows the exact Overseer architecture.",
            level="explicit",
            embedding=_vector(1.0, 0.0),
            internal_metadata={"message_created_at": now.isoformat()},
        ),
        models.Document(
            workspace_name=workspace.name,
            observer=sky.name,
            observed=bex.name,
            session_name=session.name,
            content="Sky knows a closely related Overseer milestone.",
            level="explicit",
            embedding=_vector(0.8, 0.2),
            internal_metadata={"message_created_at": now.isoformat()},
        ),
        models.Document(
            workspace_name=workspace.name,
            observer=luke.name,
            observed=bex.name,
            session_name=session.name,
            content="Luke has only a weakly related memory.",
            level="explicit",
            embedding=_vector(0.0, 1.0),
            internal_metadata={"message_created_at": now.isoformat()},
        ),
        models.Document(
            workspace_name=workspace.name,
            observer=kate.name,
            observed=bex.name,
            session_name=session.name,
            content="Kate is outside this recall allowlist.",
            level="explicit",
            embedding=_vector(1.0, 0.0),
            internal_metadata={"message_created_at": now.isoformat()},
        ),
    ]
    db_session.add_all(docs)
    await db_session.commit()

    results = await crud.query_documents_for_observers(
        db=db_session,
        workspace_name=workspace.name,
        observers=(luke.name, sarah.name, sky.name),
        observed=bex.name,
        query="Overseer",
        embedding=_vector(1.0, 0.0),
        top_k=3,
    )

    assert [document.observer for document in results] == ["sarah", "sky", "luke"]
    assert all(document.observer != "kate" for document in results)


def test_collective_evidence_deduplicates_without_losing_provenance() -> None:
    now = datetime.now(timezone.utc)
    documents = [
        models.Document(
            id="sarah-document",
            workspace_name="fam",
            observer="sarah",
            observed="bex",
            content="Overseer is Bex's voice-native engineering operator.",
            level="explicit",
            created_at=now,
            internal_metadata={"message_created_at": now.isoformat()},
        ),
        models.Document(
            id="sky-document",
            workspace_name="fam",
            observer="sky",
            observed="bex",
            content="  overseer is Bex's voice-native engineering operator.  ",
            level="explicit",
            created_at=now,
            internal_metadata={"message_created_at": now.isoformat()},
        ),
        models.Document(
            id="luke-document",
            workspace_name="fam",
            observer="luke",
            observed="bex",
            content="Overseer has a separate operator backend.",
            level="deductive",
            created_at=now,
            internal_metadata={
                "message_created_at": now.isoformat(),
                "premises": ["Bex described the operator as a separate layer."],
            },
        ),
    ]

    evidence = MemoryEvidenceSet.from_documents(documents, limit=10)

    assert len(evidence) == 2
    assert evidence.items[0].document_id == "sarah-document"
    assert evidence.items[0].document_ids == ("sarah-document", "sky-document")
    assert evidence.items[0].observers == ("sarah", "sky")
    formatted = evidence.format_as_markdown(include_ids=True)
    assert "[known by: sarah, sky]" in formatted
    assert "[id:luke-document]" in formatted


@pytest.mark.asyncio
async def test_recall_observer_resolution_uses_workspace_allowlist(
    db_session: AsyncSession,
    sample_data: tuple[models.Workspace, models.Peer],
) -> None:
    workspace, requester = sample_data
    await _add_peer(db_session, workspace.name, "sarah")
    await _add_peer(db_session, workspace.name, "sky")
    await _add_peer(db_session, workspace.name, "luke")
    await db_session.commit()

    workspace.configuration = {
        "dialectic": {
            "recall_scope": "workspace",
            "recall_observers": ["sarah", "sky", "luke"],
        }
    }
    configuration = get_configuration(None, None, workspace)

    observers = await resolve_recall_observers(
        db_session,
        workspace_name=workspace.name,
        observer=requester.name,
        configuration=configuration,
    )

    assert observers == ("sarah", "sky", "luke")


@pytest.mark.asyncio
async def test_recall_observer_resolution_rejects_unknown_peer(
    db_session: AsyncSession,
    sample_data: tuple[models.Workspace, models.Peer],
) -> None:
    workspace, requester = sample_data
    workspace.configuration = {
        "dialectic": {
            "recall_scope": "workspace",
            "recall_observers": [requester.name, "ghost"],
        }
    }
    configuration = get_configuration(None, None, workspace)

    with pytest.raises(ValidationException, match="ghost"):
        await resolve_recall_observers(
            db_session,
            workspace_name=workspace.name,
            observer=requester.name,
            configuration=configuration,
        )


@pytest.mark.asyncio
async def test_collective_message_scope_unions_sessions_and_preserves_adjacency(
    db_session: AsyncSession,
    sample_data: tuple[models.Workspace, models.Peer],
) -> None:
    workspace, _ = sample_data
    bex = await _add_peer(db_session, workspace.name, "bex")
    luke = await _add_peer(db_session, workspace.name, "luke")
    sarah = await _add_peer(db_session, workspace.name, "sarah")
    session_luke = await _add_session(
        db_session, workspace.name, "luke-session", [bex.name, luke.name]
    )
    session_sarah = await _add_session(
        db_session, workspace.name, "sarah-session", [bex.name, sarah.name]
    )

    now = datetime.now(timezone.utc)
    luke_messages = [
        models.Message(
            workspace_name=workspace.name,
            session_name=session_luke.name,
            peer_name=bex.name,
            content=f"Luke session message {seq}",
            seq_in_session=seq,
            token_count=4,
            created_at=now,
        )
        for seq in (1, 2, 3)
    ]
    sarah_messages = [
        models.Message(
            workspace_name=workspace.name,
            session_name=session_sarah.name,
            peer_name=bex.name,
            content=f"Sarah session message {seq}",
            seq_in_session=seq,
            token_count=4,
            created_at=now,
        )
        for seq in (1, 2, 3)
    ]
    db_session.add_all([*luke_messages, *sarah_messages])
    await db_session.commit()
    for message in [*luke_messages, *sarah_messages]:
        await db_session.refresh(message)

    union_messages = await crud.get_messages_by_date_range(
        db_session,
        workspace_name=workspace.name,
        session_name=None,
        observers=(luke.name, sarah.name),
        limit=20,
    )
    luke_only = await crud.get_messages_by_date_range(
        db_session,
        workspace_name=workspace.name,
        session_name=None,
        observer=luke.name,
        limit=20,
    )

    assert {message.session_name for message in union_messages} == {
        session_luke.name,
        session_sarah.name,
    }
    assert {message.session_name for message in luke_only} == {session_luke.name}

    context = await get_observation_context(
        db_session,
        workspace_name=workspace.name,
        session_name=None,
        message_ids=[luke_messages[1].public_id],
        observers=(luke.name, sarah.name),
    )

    assert [message.content for message in context] == [
        "Luke session message 1",
        "Luke session message 2",
        "Luke session message 3",
    ]
