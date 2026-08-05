"""Tests for workspace-scoped canonical peer identity."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import crud, models, schemas
from src.exceptions import ValidationException
from src.identity import canonicalize_peer_id, get_identity_configuration


def _fam_identity() -> schemas.ResolvedIdentityConfiguration:
    return schemas.ResolvedIdentityConfiguration(
        canonical_case=schemas.PeerCanonicalCase.LOWER,
        strict=True,
        allow_dynamic_peers=False,
        aliases={
            "bex": "bex",
            "rebecca_clair": "bex",
            "sarah": "sarah",
            "sky": "sky",
            "luke": "luke",
            "kate": "kate",
            "codex": "kate",
            "agent-codex": "kate",
        },
    )


def test_aliases_resolve_case_insensitively() -> None:
    configuration = _fam_identity()

    assert canonicalize_peer_id(" BEX ", configuration) == "bex"
    assert canonicalize_peer_id("Rebecca_Clair", configuration) == "bex"
    assert canonicalize_peer_id("Codex", configuration) == "kate"
    assert canonicalize_peer_id("agent-codex", configuration) == "kate"


def test_strict_identity_rejects_unknown_peers() -> None:
    with pytest.raises(ValidationException, match="Unknown peer identity"):
        canonicalize_peer_id("temporary-worker", _fam_identity())


def test_lowercase_dynamic_policy_is_safe_and_deterministic() -> None:
    configuration = schemas.ResolvedIdentityConfiguration(
        canonical_case=schemas.PeerCanonicalCase.LOWER,
        strict=False,
        allow_dynamic_peers=True,
    )

    assert canonicalize_peer_id("  New_Worker  ", configuration) == "new_worker"
    with pytest.raises(ValidationException, match="cannot be canonicalized safely"):
        canonicalize_peer_id("New Worker", configuration)


@pytest.mark.asyncio
async def test_peer_creation_collapses_aliases_into_canonical_peer(
    db_session: AsyncSession,
    sample_data: tuple[models.Workspace, models.Peer],
) -> None:
    workspace, _ = sample_data
    workspace.configuration = {
        "identity": {
            "canonical_case": "lower",
            "strict": True,
            "allow_dynamic_peers": False,
            "aliases": {
                "Bex": "bex",
                "bex": "bex",
                "Rebecca_Clair": "bex",
                "Kate": "kate",
                "codex": "kate",
                "agent-codex": "kate",
            },
        }
    }
    await db_session.commit()

    result = await crud.get_or_create_peers(
        db_session,
        workspace.name,
        [
            schemas.PeerCreate(name="Bex"),
            schemas.PeerCreate(name="Rebecca_Clair"),
            schemas.PeerCreate(name="codex"),
            schemas.PeerCreate(name="Kate"),
        ],
    )
    await db_session.commit()
    await result.post_commit()

    assert [peer.name for peer in result.resource] == ["bex", "kate"]
    peers_stmt = await crud.get_peers(workspace.name)
    peers = (await db_session.execute(peers_stmt)).scalars().all()
    names = {peer.name for peer in peers}
    assert {"bex", "kate"}.issubset(names)
    assert not {"Bex", "Rebecca_Clair", "codex"}.intersection(names)


@pytest.mark.asyncio
async def test_message_ingestion_uses_canonical_author(
    db_session: AsyncSession,
    sample_data: tuple[models.Workspace, models.Peer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = sample_data
    workspace.configuration = {
        "identity": {
            "canonical_case": "lower",
            "strict": True,
            "allow_dynamic_peers": False,
            "aliases": {
                "Bex": "bex",
                "bex": "bex",
                "Sarah": "sarah",
                "sarah": "sarah",
            },
        }
    }
    await db_session.commit()
    monkeypatch.setattr("src.crud.message.settings.EMBED_MESSAGES", False)

    messages = await crud.create_messages(
        db_session,
        workspace_name=workspace.name,
        session_name="identity-session",
        messages=[
            schemas.MessageCreate(
                peer_id="Bex",
                content="The canonical author is Bex.",
                created_at=datetime.now(timezone.utc),
            ),
            schemas.MessageCreate(
                peer_id="Sarah",
                content="The canonical author is Sarah.",
                created_at=datetime.now(timezone.utc),
            ),
        ],
    )

    assert [message.peer_name for message in messages] == ["bex", "sarah"]


@pytest.mark.asyncio
async def test_identity_configuration_normalizes_alias_keys(
    db_session: AsyncSession,
    sample_data: tuple[models.Workspace, models.Peer],
) -> None:
    workspace, _ = sample_data
    workspace.configuration = {
        "identity": {
            "canonical_case": "lower",
            "strict": True,
            "allow_dynamic_peers": False,
            "aliases": {" BEX ": "bex", "Rebecca_Clair": "bex"},
        }
    }
    await db_session.commit()

    configuration = await get_identity_configuration(db_session, workspace.name)

    assert configuration.aliases == {"bex": "bex", "rebecca_clair": "bex"}
