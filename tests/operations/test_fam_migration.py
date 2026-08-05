"""Synthetic end-to-end migration into the canonical fam workspace."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from nanoid import generate as generate_nanoid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import models
from src.config import settings
from src.operations.fam_migration import (
    FamManifest,
    SourceWorkspace,
    apply_migration,
    build_plan,
)


def _vector(seed: float) -> list[float]:
    return [seed] * settings.EMBEDDING.VECTOR_DIMENSIONS


def _manifest() -> FamManifest:
    aliases = {
        "Bex": "bex",
        "bex": "bex",
        "sarah": "sarah",
        "Sarah": "sarah",
        "sky": "sky",
        "luke": "luke",
        "agent-codex": "kate",
        "codex": "kate",
        "kate": "kate",
    }
    return FamManifest(
        target_workspace="fam",
        canonical_peers={
            peer: {"display_name": peer.title()}
            for peer in ("bex", "sarah", "sky", "luke", "kate")
        },
        workspace_metadata={"agentPeerMap": {"sky": "sky", "codex": "kate"}},
        workspace_configuration={
            "identity": {
                "canonical_case": "lower",
                "strict": True,
                "allow_dynamic_peers": False,
                "aliases": aliases,
            },
            "dialectic": {
                "recall_scope": "workspace",
                "recall_observers": ["bex", "sarah", "sky", "luke", "kate"],
            },
        },
        sources=(
            SourceWorkspace(
                "openclaw",
                {
                    "Bex": "bex",
                    "sarah": "sarah",
                    "sky": "sky",
                    "luke": "luke",
                    "agent-codex": "kate",
                },
                frozenset({"oc-archive"}),
            ),
            SourceWorkspace(
                "hermes",
                {"bex": "bex", "Sarah": "sarah", "luke": "luke"},
                frozenset({"-hermes"}),
            ),
        ),
        peer_card_authority={
            "bex": ("openclaw", "Bex"),
            "sarah": ("openclaw", "sarah"),
            "sky": ("openclaw", "sky"),
            "luke": ("hermes", "luke"),
            "kate": ("openclaw", "agent-codex"),
        },
    )


async def _seed(db: AsyncSession) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    db.add_all([models.Workspace(name="openclaw"), models.Workspace(name="hermes")])
    await db.flush()

    peer_cards = {
        ("openclaw", "Bex"): ["IDENTITY: Name: Bex"],
        ("openclaw", "sarah"): ["IDENTITY: Name: Sarah"],
        ("openclaw", "sky"): ["IDENTITY: Name: Sky"],
        ("openclaw", "luke"): ["IDENTITY: Runtime: OpenClaw"],
        ("openclaw", "agent-codex"): ["IDENTITY: Name: Kate"],
        ("hermes", "bex"): ["IDENTITY: Name: Bex"],
        ("hermes", "Sarah"): ["IDENTITY: Name: Sarah"],
        ("hermes", "luke"): ["IDENTITY: Name: Luke", "IDENTITY: Runtime: Hermes"],
        ("hermes", "test-peer"): ["IDENTITY: Test only"],
    }
    for (workspace, name), card in peer_cards.items():
        internal = {"peer_card": card}
        if (workspace, name) == ("hermes", "luke"):
            internal["bex_peer_card"] = ["RELATIONSHIP: Bex: trusted companion"]
        db.add(
            models.Peer(workspace_name=workspace, name=name, internal_metadata=internal)
        )
    await db.flush()

    sessions = {
        "oc-main": models.Session(
            id=generate_nanoid(), name="oc-main", workspace_name="openclaw"
        ),
        "oc-archive": models.Session(
            id=generate_nanoid(), name="oc-archive", workspace_name="openclaw"
        ),
        "hermes-main": models.Session(
            id=generate_nanoid(), name="hermes-main", workspace_name="hermes"
        ),
        "-hermes": models.Session(
            id=generate_nanoid(), name="-hermes", workspace_name="hermes"
        ),
    }
    db.add_all(sessions.values())
    await db.flush()

    memberships = {
        "oc-main": ("openclaw", ["Bex", "sarah", "sky", "luke", "agent-codex"]),
        "oc-archive": ("openclaw", ["sky"]),
        "hermes-main": ("hermes", ["bex", "Sarah", "luke"]),
        "-hermes": ("hermes", ["Sarah", "test-peer"]),
    }
    for session_name, (workspace, peers) in memberships.items():
        for peer in peers:
            db.add(
                models.SessionPeer(
                    workspace_name=workspace,
                    session_name=session_name,
                    peer_name=peer,
                    configuration={"observe_me": True, "observe_others": peer != "Bex"},
                    joined_at=now,
                )
            )
    await db.flush()

    message_specs = [
        ("openclaw", "oc-main", "Bex", "Bex described Overseer.", 1),
        ("openclaw", "oc-main", "agent-codex", "Kate prepared the code review.", 2),
        ("openclaw", "oc-main", "luke", "Legacy Luke remembered an older detail.", 3),
        ("hermes", "hermes-main", "bex", "Bex spoke with current Luke.", 1),
        ("hermes", "hermes-main", "luke", "Current Luke answered from Hermes.", 2),
        ("hermes", "-hermes", "test-peer", "Synthetic test traffic.", 1),
    ]
    messages: list[models.Message] = []
    for workspace, session, peer, content, seq in message_specs:
        message = models.Message(
            workspace_name=workspace,
            session_name=session,
            peer_name=peer,
            content=content,
            seq_in_session=seq,
            token_count=4,
        )
        db.add(message)
        messages.append(message)
    await db.flush()
    original_messages = {
        message.content: (message.id, message.public_id) for message in messages
    }
    db.add(
        models.MessageEmbedding(
            workspace_name="openclaw",
            session_name="oc-main",
            peer_name="agent-codex",
            message_id=messages[1].public_id,
            content=messages[1].content,
            embedding=_vector(0.2),
        )
    )

    collection_specs = [
        ("openclaw", "sarah", "Bex"),
        ("openclaw", "agent-codex", "Bex"),
        ("hermes", "luke", "bex"),
        ("hermes", "Sarah", "bex"),
        ("hermes", "test-peer", "Sarah"),
    ]
    for workspace, observer, observed in collection_specs:
        db.add(
            models.Collection(
                workspace_name=workspace, observer=observer, observed=observed
            )
        )
    await db.flush()

    explicit = models.Document(
        id=generate_nanoid(),
        workspace_name="openclaw",
        session_name="oc-main",
        observer="sarah",
        observed="Bex",
        content="Overseer is voice-native.",
        embedding=_vector(0.3),
        level="explicit",
    )
    derived = models.Document(
        id=generate_nanoid(),
        workspace_name="openclaw",
        session_name="oc-main",
        observer="agent-codex",
        observed="Bex",
        content="Overseer separates operator execution.",
        embedding=_vector(0.4),
        level="deductive",
        source_ids=[explicit.id],
    )
    hermes_doc = models.Document(
        id=generate_nanoid(),
        workspace_name="hermes",
        session_name="hermes-main",
        observer="luke",
        observed="bex",
        content="Luke runs in Hermes.",
        embedding=_vector(0.5),
        level="explicit",
    )
    global_doc = models.Document(
        id=generate_nanoid(),
        workspace_name="hermes",
        session_name=None,
        observer="Sarah",
        observed="bex",
        content="Bex prefers lowercase IDs.",
        embedding=_vector(0.6),
        level="explicit",
    )
    archived_doc = models.Document(
        id=generate_nanoid(),
        workspace_name="hermes",
        session_name="-hermes",
        observer="test-peer",
        observed="Sarah",
        content="Archived test conclusion.",
        embedding=_vector(0.7),
        level="explicit",
    )
    db.add_all([explicit, derived, hermes_doc, global_doc, archived_doc])
    await db.flush()

    queue = models.QueueItem(
        session_id=sessions["oc-main"].id,
        work_unit_key=f"representation:openclaw:{sessions['oc-main'].id}",
        task_type="representation",
        payload={
            "task_type": "representation",
            "session_name": "oc-main",
            "observed": "Bex",
            "observers": ["sarah", "agent-codex"],
            "configuration": {},
            "content": [],
            "created_at": now.isoformat(),
        },
        processed=True,
        workspace_name="openclaw",
        message_id=messages[0].id,
    )
    dead = models.QueueDeadLetter(
        id=generate_nanoid(),
        original_queue_id=None,
        session_id=sessions["hermes-main"].id,
        work_unit_key=f"representation:hermes:{sessions['hermes-main'].id}",
        task_type="representation",
        payload={
            "task_type": "representation",
            "session_name": "hermes-main",
            "observed": "bex",
            "observers": ["luke"],
            "configuration": {},
            "content": [],
            "created_at": now.isoformat(),
        },
        workspace_name="hermes",
        message_id=messages[3].id,
        attempts=3,
        error="synthetic",
    )
    db.add_all([queue, dead])
    await db.commit()
    return {
        "messages": original_messages,
        "docs": {
            "explicit": explicit.id,
            "derived": derived.id,
            "hermes": hermes_doc.id,
            "global": global_doc.id,
            "archived": archived_doc.id,
        },
        "queue": queue.id,
        "dead": dead.id,
    }


@pytest.mark.asyncio
async def test_fam_migration_preserves_ids_and_authoritative_luke(
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session)
    manifest = _manifest()
    plan = await build_plan(db_session, manifest)
    assert len(plan.sessions) == 2

    result = await apply_migration(db_session, manifest)
    await db_session.commit()
    assert result.report["verification"]["sessions"] == 2

    workspace = await db_session.scalar(
        select(models.Workspace).where(models.Workspace.name == "fam")
    )
    assert workspace and workspace.h_metadata["famMigrationState"] == "complete"
    assert workspace.h_metadata["agentPeerMap"] == {"sky": "sky", "codex": "kate"}

    peers = (
        (
            await db_session.execute(
                select(models.Peer).where(models.Peer.workspace_name == "fam")
            )
        )
        .scalars()
        .all()
    )
    assert {peer.name for peer in peers} == {"bex", "sarah", "sky", "luke", "kate"}
    luke = next(peer for peer in peers if peer.name == "luke")
    assert luke.internal_metadata["peer_card"] == [
        "IDENTITY: Name: Luke",
        "IDENTITY: Runtime: Hermes",
    ]

    migrated_messages = (
        (
            await db_session.execute(
                select(models.Message).where(models.Message.workspace_name == "fam")
            )
        )
        .scalars()
        .all()
    )
    assert {message.peer_name for message in migrated_messages} == {
        "bex",
        "kate",
        "luke",
    }
    for message in migrated_messages:
        assert (message.id, message.public_id) == seeded["messages"][message.content]

    docs = (
        (
            await db_session.execute(
                select(models.Document).where(models.Document.workspace_name == "fam")
            )
        )
        .scalars()
        .all()
    )
    assert {doc.id for doc in docs} == {
        seeded["docs"][key] for key in ("explicit", "derived", "hermes", "global")
    }
    derived = next(doc for doc in docs if doc.level == "deductive")
    assert derived.observer == "kate" and derived.observed == "bex"
    assert derived.source_ids == [seeded["docs"]["explicit"]]

    archived = await db_session.get(models.Document, seeded["docs"]["archived"])
    assert archived and archived.workspace_name == "hermes"
    assert await db_session.scalar(
        select(models.Session).where(
            models.Session.workspace_name == "hermes", models.Session.name == "-hermes"
        )
    )

    queue = await db_session.get(models.QueueItem, seeded["queue"])
    assert queue and queue.workspace_name == "fam"
    assert queue.payload["observed"] == "bex"
    assert queue.payload["observers"] == ["sarah", "kate"]
    dead = await db_session.get(models.QueueDeadLetter, seeded["dead"])
    assert dead and dead.workspace_name == "fam"

    for source in ("openclaw", "hermes"):
        old = await db_session.scalar(
            select(models.Workspace).where(models.Workspace.name == source)
        )
        assert old and old.h_metadata["famMigrationState"] == "archived"
        assert old.configuration["reasoning"]["enabled"] is False
