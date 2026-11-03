"""Synthetic end-to-end migration into the canonical fam workspace."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from nanoid import generate as generate_nanoid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import models
from src.config import settings
from src.exceptions import ValidationException
from src.identity import resolve_peer_id
from src.operations.fam_migration import (
    FamManifest,
    FamMigrationError,
    SourceWorkspace,
    _merge_peer_rows,  # pyright: ignore[reportPrivateUsage]
    _rewrite_work_unit_key,  # pyright: ignore[reportPrivateUsage]
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
        processed=False,
        workspace_name="openclaw",
        message_id=messages[0].id,
        attempts=2,
        error="preserve pending failure",
        next_attempt_at=now,
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
        "queue_next_attempt_at": now,
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
    assert result.report["verification"]["pending_queue_rows"] == 1
    assert result.report["verification"]["queue_rows_with_error"] == 1
    assert result.report["verification"]["dead_letter_rows"] == 1

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
    assert queue.processed is False
    assert queue.attempts == 2
    assert queue.error == "preserve pending failure"
    assert queue.next_attempt_at == seeded["queue_next_attempt_at"]
    dead = await db_session.get(models.QueueDeadLetter, seeded["dead"])
    assert dead and dead.workspace_name == "fam"

    for source in ("openclaw", "hermes"):
        old = await db_session.scalar(
            select(models.Workspace).where(models.Workspace.name == source)
        )
        assert old and old.h_metadata["famMigrationState"] == "archived"
        assert old.configuration["reasoning"]["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "occupied",
    [
        "peer",
        "session",
        "queue",
        "marker",
        "metadata-only",
        "config-only",
        "internal-marker",
    ],
)
async def test_fam_rejects_nonpristine_destination(
    db_session: AsyncSession, occupied: str
) -> None:
    await _seed(db_session)
    target = models.Workspace(name="fam")
    if occupied == "marker":
        target.h_metadata = {"famMigrationState": "running"}
    elif occupied == "metadata-only":
        target.h_metadata = {"owner": "existing-owner"}
    elif occupied == "config-only":
        target.configuration = {"reasoning": {"enabled": False}}
    elif occupied == "internal-marker":
        target.internal_metadata = {"fam_migration": {"state": "running"}}
    db_session.add(target)
    await db_session.flush()
    if occupied == "peer":
        db_session.add(models.Peer(workspace_name="fam", name="legacy"))
    elif occupied == "session":
        db_session.add(models.Session(workspace_name="fam", name="preexisting"))
    elif occupied == "queue":
        db_session.add(
            models.QueueItem(
                workspace_name="fam",
                work_unit_key="dream:fam",
                task_type="dream",
                payload={},
            )
        )
    await db_session.flush()
    with pytest.raises(FamMigrationError, match="not pristine"):
        await apply_migration(db_session, _manifest())
    assert await db_session.scalar(
        select(models.Message)
        .where(models.Message.workspace_name == "openclaw")
        .limit(1)
    )


@pytest.mark.asyncio
async def test_fam_all_workspaces_preserves_distinct_peers_and_entire_corpus(
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session)
    target = models.Workspace(name="fam")
    db_session.add_all(
        [target, models.Workspace(name="pi"), models.Workspace(name="opencode")]
    )
    await db_session.flush()
    original_target_id = target.id
    legacy_internal = {
        "peer_card": ["IDENTITY: Legacy uncertain"],
        "unidentified_peer_card": ["RELATIONSHIP: preserve this exact card"],
        "custom": {"keep": True},
    }
    db_session.add_all(
        [
            models.Peer(
                workspace_name="hermes",
                name="zero-message",
                h_metadata={"display_name": "Legacy companion"},
                internal_metadata=legacy_internal,
            ),
            models.Peer(workspace_name="pi", name="pi-sky"),
            models.Peer(
                workspace_name="opencode",
                name="user-opencode-sky",
                internal_metadata=legacy_internal,
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        models.Collection(
            workspace_name="hermes",
            observer="zero-message",
            observed="bex",
            h_metadata={"retained": "empty collection"},
            internal_metadata={"last_dream": "preserve me"},
        )
    )
    db_session.add_all(
        [
            models.Session(workspace_name="pi", name="pi-main"),
            models.Session(workspace_name="opencode", name="opencode-main"),
        ]
    )
    await db_session.flush()
    for workspace, peer in [("pi", "pi-sky"), ("opencode", "user-opencode-sky")]:
        session = f"{workspace}-main"
        db_session.add(
            models.SessionPeer(
                workspace_name=workspace, session_name=session, peer_name=peer
            )
        )
        db_session.add(
            models.Message(
                workspace_name=workspace,
                session_name=session,
                peer_name=peer,
                content=f"{workspace} history",
                seq_in_session=1,
                token_count=2,
            )
        )
    await db_session.flush()
    messages = (await db_session.scalars(select(models.Message))).all()
    before_messages = {
        message.id: (
            message.public_id,
            message.content,
            message.token_count,
            message.seq_in_session,
        )
        for message in messages
    }
    documents = (await db_session.scalars(select(models.Document))).all()
    before_documents = {
        doc.id: (doc.content, list(doc.embedding), doc.source_ids) for doc in documents
    }
    embeddings = (await db_session.scalars(select(models.MessageEmbedding))).all()
    before_embeddings = {
        row.id: (row.message_id, row.content, list(row.embedding)) for row in embeddings
    }
    await db_session.commit()

    base = _manifest()
    configuration = dict(base.workspace_configuration)
    configuration["identity"] = dict(configuration["identity"])
    configuration["identity"]["aliases"] = {
        **configuration["identity"]["aliases"],
        "test-peer": "test-peer",
        "zero-message": "zero-message",
        "user-opencode-sky": "user-opencode-sky",
    }
    manifest = replace(
        base,
        workspace_configuration=configuration,
        sources=(
            replace(base.sources[0], leave_sessions=frozenset()),
            replace(
                base.sources[1],
                leave_sessions=frozenset(),
                preserve_peers=frozenset({"test-peer", "zero-message"}),
            ),
            SourceWorkspace("pi", {"pi-sky": "sky"}, frozenset()),
            SourceWorkspace(
                "opencode", {}, frozenset(), frozenset({"user-opencode-sky"})
            ),
        ),
    )
    plan = await build_plan(db_session, manifest)
    assert len(plan.sessions) == 6
    assert ("zero-message", "bex") in plan.collection_pairs
    assert plan.report["sources"]["hermes"]["peer_dispositions"]["zero-message"] == {
        "target": "zero-message",
        "disposition": "preserve-as-distinct",
    }
    result = await apply_migration(db_session, manifest)
    await db_session.commit()
    assert result.report["verification"]["messages"] == len(before_messages)
    assert result.report["verification"]["documents"] == len(before_documents)
    db_session.expire_all()
    migrated_target = await db_session.scalar(
        select(models.Workspace).where(models.Workspace.name == "fam")
    )
    assert migrated_target and migrated_target.id == original_target_id
    for message in await db_session.scalars(select(models.Message)):
        assert message.workspace_name == "fam"
        assert (
            message.public_id,
            message.content,
            message.token_count,
            message.seq_in_session,
        ) == before_messages[message.id]
    for doc in await db_session.scalars(select(models.Document)):
        assert doc.workspace_name == "fam"
        assert (doc.content, list(doc.embedding), doc.source_ids) == before_documents[
            doc.id
        ]
    migrated_embeddings = (
        await db_session.scalars(select(models.MessageEmbedding))
    ).all()
    assert {row.id for row in migrated_embeddings} == set(before_embeddings)
    for row in migrated_embeddings:
        assert row.workspace_name == "fam"
        assert (row.message_id, row.content, list(row.embedding)) == before_embeddings[
            row.id
        ]
    archived_doc = await db_session.get(models.Document, seeded["docs"]["archived"])
    assert archived_doc and archived_doc.observer == "test-peer"
    peers = (
        await db_session.scalars(
            select(models.Peer).where(models.Peer.workspace_name == "fam")
        )
    ).all()
    assert {peer.name for peer in peers} == manifest.target_peers
    assert (
        next(peer for peer in peers if peer.name == "zero-message").h_metadata[
            "display_name"
        ]
        == "Legacy companion"
    )
    empty_collection = await db_session.scalar(
        select(models.Collection).where(
            models.Collection.workspace_name == "fam",
            models.Collection.observer == "zero-message",
            models.Collection.observed == "bex",
        )
    )
    assert empty_collection
    assert empty_collection.h_metadata == {"retained": "empty collection"}
    assert empty_collection.internal_metadata == {"last_dream": "preserve me"}
    for name in ("zero-message", "user-opencode-sky"):
        peer = next(peer for peer in peers if peer.name == name)
        assert (
            peer.internal_metadata["unidentified_peer_card"]
            == legacy_internal["unidentified_peer_card"]
        )
        assert (
            peer.internal_metadata["fam_migration"]["sources"][0]["internal_metadata"]
            == legacy_internal
        )
        assert await resolve_peer_id(db_session, "fam", name) == name
    with pytest.raises(ValidationException, match="Unknown peer identity"):
        await resolve_peer_id(db_session, "fam", "arbitrary-new-peer")
    for workspace in manifest.source_names:
        assert not await db_session.scalar(
            select(models.Session).where(models.Session.workspace_name == workspace)
        )


@pytest.mark.asyncio
async def test_fam_all_workspaces_requires_disposition_for_zero_message_peer(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    base = _manifest()
    manifest = replace(
        base,
        sources=tuple(
            replace(source, leave_sessions=frozenset()) for source in base.sources
        ),
    )
    with pytest.raises(
        FamMigrationError, match="without an explicit disposition.*test-peer"
    ):
        await build_plan(db_session, manifest)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (
            "representation:opencode:opencode-main:user-opencode-sky",
            "representation:fam:opencode-main:user-opencode-sky",
        ),
        (
            "representation:opencode:opencode-main:old-sky:user-opencode-sky",
            "representation:fam:opencode-main:sky:user-opencode-sky",
        ),
        (
            "summary:opencode:opencode-main:old-sky:user-opencode-sky",
            "summary:fam:opencode-main:sky:user-opencode-sky",
        ),
        (
            "dream:opencode-dream:opencode:old-sky:user-opencode-sky",
            "dream:opencode-dream:fam:sky:user-opencode-sky",
        ),
        ("webhook:opencode", "webhook:fam"),
        (
            "deletion:opencode:session:opencode-main",
            "deletion:fam:session:opencode-main",
        ),
        (
            "representation:opencode:old-session-id:old-sky",
            "representation:fam:old-session-id:sky",
        ),
        (
            "representation:opencode:old-session-id:old-sky:user-opencode-sky",
            "representation:fam:old-session-id:sky:user-opencode-sky",
        ),
        (
            "summary:opencode:old-session-id:old-sky:user-opencode-sky",
            "summary:fam:old-session-id:sky:user-opencode-sky",
        ),
        ("representation:opencode:old-session-id", "representation:fam:new-session-id"),
        ("reconciler:opencode-sync", "reconciler:opencode-sync"),
    ],
)
def test_fam_work_unit_key_rewrites_only_identity_fields(
    key: str, expected: str
) -> None:
    assert (
        _rewrite_work_unit_key(
            key,
            source_workspace="opencode",
            target_workspace="fam",
            session_ids={"old-session-id": "new-session-id"},
            peer_map={"old-sky": "sky", "user-opencode-sky": "user-opencode-sky"},
        )
        == expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel", ["None", None])
async def test_fam_queue_empty_history_preserves_null_sentinel(
    db_session: AsyncSession, sentinel: str | None
) -> None:
    await _seed(db_session)
    payload: dict[str, Any] = {
        "task_type": "webhook",
        "event_type": "queue.empty",
        "data": {
            "workspace_name": "openclaw",
            "observer": sentinel,
            "observed": sentinel,
        },
    }
    historical = [
        models.QueueItem(
            workspace_name="openclaw",
            work_unit_key="webhook:openclaw",
            task_type="webhook",
            payload=payload,
            processed=True,
            error="historical delivery error",
            attempts=3,
        )
        for _ in range(7)
    ]
    dead = models.QueueDeadLetter(
        workspace_name="openclaw",
        work_unit_key="webhook:openclaw",
        task_type="webhook",
        payload=payload,
        error="historical dead letter",
        attempts=4,
    )
    db_session.add_all([*historical, dead])
    await db_session.commit()
    historical_ids = [row.id for row in historical]
    dead_id = dead.id
    await apply_migration(db_session, _manifest())
    await db_session.commit()
    db_session.expire_all()
    expected_payload = {**payload, "data": {**payload["data"], "workspace_name": "fam"}}
    for row_id in historical_ids:
        row = await db_session.get(models.QueueItem, row_id)
        assert row and row.workspace_name == "fam"
        assert row.payload == expected_payload
        assert row.work_unit_key == "webhook:fam"
        assert (
            row.processed is True
            and row.error == "historical delivery error"
            and row.attempts == 3
        )
    migrated_dead = await db_session.get(models.QueueDeadLetter, dead_id)
    assert migrated_dead and migrated_dead.workspace_name == "fam"
    assert migrated_dead.payload == expected_payload
    assert (
        migrated_dead.error == "historical dead letter" and migrated_dead.attempts == 4
    )
    assert not await db_session.scalar(
        select(models.Peer).where(models.Peer.name == "None")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel", ["None", None, "sarah"])
async def test_fam_webhook_session_disposition_precedes_peer_mapping(
    db_session: AsyncSession, sentinel: str | None
) -> None:
    await _seed(db_session)
    rows = []
    for model in (models.QueueItem, models.QueueDeadLetter):
        for session_name in ("oc-main", "oc-archive"):
            row = model(
                workspace_name="openclaw",
                work_unit_key="webhook:openclaw",
                task_type="webhook",
                payload={
                    "task_type": "webhook",
                    "event_type": "queue.empty",
                    "data": {
                        "workspace_id": "openclaw",
                        "session_id": session_name,
                        "observer": sentinel,
                        "observed": sentinel,
                    },
                },
                error="retained history",
                attempts=3,
            )
            db_session.add(row)
            rows.append((row, session_name))
    await db_session.commit()
    row_ids = [(type(row), row.id, session) for row, session in rows]
    await apply_migration(db_session, _manifest())
    await db_session.commit()
    db_session.expire_all()
    for model, row_id, session in row_ids:
        row = await db_session.get(model, row_id)
        assert row
        expected_workspace = "fam" if session == "oc-main" else "openclaw"
        assert row.workspace_name == expected_workspace
        assert row.payload["data"]["workspace_id"] == expected_workspace
        assert row.payload["data"]["session_id"] == session
        assert row.error == "retained history"


@pytest.mark.asyncio
async def test_fam_session_deletions_follow_selected_session_names(
    db_session: AsyncSession,
) -> None:
    from src.deriver.enqueue import create_deletion_record

    await _seed(db_session)
    rows = []
    for model in (models.QueueItem, models.QueueDeadLetter):
        for session_name in ("oc-main", "oc-archive"):
            row = model(
                **create_deletion_record("openclaw", "session", session_name),
                error="retained deletion failure",
                attempts=4,
            )
            db_session.add(row)
            rows.append((row, session_name))
    await db_session.commit()
    row_ids = [(type(row), row.id, session) for row, session in rows]
    await apply_migration(db_session, _manifest())
    await db_session.commit()
    db_session.expire_all()
    for model, row_id, session in row_ids:
        row = await db_session.get(model, row_id)
        assert row
        expected_workspace = "fam" if session == "oc-main" else "openclaw"
        assert row.workspace_name == expected_workspace
        assert row.work_unit_key == f"deletion:{expected_workspace}:session:{session}"
        assert row.payload["resource_id"] == session
        assert row.error == "retained deletion failure" and row.attempts == 4


@pytest.mark.asyncio
async def test_fam_preflight_rejects_converging_pending_dream_keys(
    db_session: AsyncSession,
) -> None:
    from src.schemas import DreamType
    from src.utils.queue_payload import create_dream_payload
    from src.utils.work_unit import construct_work_unit_key

    await _seed(db_session)
    rows = []
    for workspace, observer, observed in (
        ("openclaw", "sarah", "Bex"),
        ("hermes", "Sarah", "bex"),
    ):
        payload = create_dream_payload(
            DreamType.OMNI, observer=observer, observed=observed
        )
        row = models.QueueItem(
            workspace_name=workspace,
            task_type="dream",
            payload=payload,
            work_unit_key=construct_work_unit_key(workspace, payload),
        )
        db_session.add(row)
        rows.append(row)
    await db_session.commit()
    with pytest.raises(FamMigrationError, match="pending dream.*collision") as error:
        await build_plan(db_session, _manifest())
    for row in rows:
        assert str(row.id) in str(error.value)
    assert not await db_session.scalar(
        select(models.Workspace).where(models.Workspace.name == "fam")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("peer_name", ["LegacyPeer", "missing-alias"])
async def test_fam_preflight_rejects_unresolvable_preserved_identity(
    db_session: AsyncSession, peer_name: str
) -> None:
    await _seed(db_session)
    db_session.add(models.Peer(workspace_name="hermes", name=peer_name))
    await db_session.commit()
    base = _manifest()
    configuration = copy.deepcopy(base.workspace_configuration)
    if peer_name == "LegacyPeer":
        configuration["identity"]["aliases"][peer_name] = peer_name
    manifest = replace(
        base,
        workspace_configuration=configuration,
        sources=(
            base.sources[0],
            replace(base.sources[1], preserve_peers=frozenset({peer_name})),
        ),
    )
    with pytest.raises(FamMigrationError, match="identity"):
        await build_plan(db_session, manifest)


def test_fam_preserved_peer_retains_active_display_name() -> None:
    manifest = _manifest()
    manifest = replace(
        manifest,
        sources=(
            manifest.sources[0],
            replace(manifest.sources[1], preserve_peers=frozenset({"legacy-peer"})),
        ),
    )
    peer = _merge_peer_rows(
        "legacy-peer",
        [
            {
                "id": "original-peer-id",
                "workspace_name": "hermes",
                "name": "legacy-peer",
                "created_at": datetime.now(timezone.utc),
                "metadata": {"display_name": "Legacy companion"},
            }
        ],
        manifest,
    )
    assert peer["metadata"]["display_name"] == "Legacy companion"
