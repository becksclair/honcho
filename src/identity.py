"""Workspace-scoped peer identity canonicalization."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import models, schemas
from src.exceptions import ValidationException

_T = TypeVar("_T")

_CANONICAL_PEER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def normalize_presented_peer_id(peer_id: str) -> str:
    """Normalize transport noise without deciding the canonical identity."""
    normalized = unicodedata.normalize("NFKC", peer_id).strip()
    if not normalized:
        raise ValidationException("Peer ID cannot be blank")
    return normalized


def canonicalize_peer_id(
    peer_id: str,
    configuration: schemas.ResolvedIdentityConfiguration,
) -> str:
    """Resolve one presented peer ID through a workspace identity policy."""
    presented = normalize_presented_peer_id(peer_id)
    alias_key = presented.casefold()
    canonical = configuration.aliases.get(alias_key)
    if canonical is not None:
        return canonical

    if configuration.strict or not configuration.allow_dynamic_peers:
        raise ValidationException(
            f"Unknown peer identity {peer_id!r}; register an alias before use"
        )

    if configuration.canonical_case == schemas.PeerCanonicalCase.LOWER:
        candidate = alias_key
        if not _CANONICAL_PEER_ID_PATTERN.fullmatch(candidate):
            raise ValidationException(
                f"Dynamic peer identity {peer_id!r} cannot be canonicalized safely"
            )
        return candidate

    return presented


def canonicalize_peer_ids(
    peer_ids: Iterable[str],
    configuration: schemas.ResolvedIdentityConfiguration,
) -> tuple[str, ...]:
    """Resolve and stably deduplicate several presented peer IDs."""
    return tuple(
        dict.fromkeys(
            canonicalize_peer_id(peer_id, configuration) for peer_id in peer_ids
        )
    )


async def get_identity_configuration(
    db: AsyncSession,
    workspace_name: str,
) -> schemas.ResolvedIdentityConfiguration:
    """Read and resolve the workspace identity policy without creating state."""
    raw_configuration = await db.scalar(
        select(models.Workspace.configuration).where(
            models.Workspace.name == workspace_name
        )
    )
    if not raw_configuration:
        return schemas.ResolvedIdentityConfiguration()

    workspace_configuration = schemas.WorkspaceConfiguration.model_validate(
        raw_configuration
    )
    identity = workspace_configuration.identity
    if identity is None:
        return schemas.ResolvedIdentityConfiguration()

    return schemas.ResolvedIdentityConfiguration(
        canonical_case=identity.canonical_case or schemas.PeerCanonicalCase.PRESERVE,
        strict=identity.strict if identity.strict is not None else False,
        allow_dynamic_peers=(
            identity.allow_dynamic_peers
            if identity.allow_dynamic_peers is not None
            else True
        ),
        aliases=identity.aliases or {},
    )


async def resolve_peer_id(
    db: AsyncSession,
    workspace_name: str,
    peer_id: str,
) -> str:
    """Resolve a presented peer ID using the workspace's active policy."""
    configuration = await get_identity_configuration(db, workspace_name)
    return canonicalize_peer_id(peer_id, configuration)


async def resolve_peer_ids(
    db: AsyncSession,
    workspace_name: str,
    peer_ids: Iterable[str],
) -> tuple[str, ...]:
    """Resolve several peer IDs with one workspace configuration read."""
    configuration = await get_identity_configuration(db, workspace_name)
    return canonicalize_peer_ids(peer_ids, configuration)


async def resolve_peer_mapping(
    db: AsyncSession,
    workspace_name: str,
    peer_mapping: Mapping[str, _T],
) -> dict[str, _T]:
    """Canonicalize mapping keys and reject conflicting alias collisions."""
    configuration = await get_identity_configuration(db, workspace_name)
    resolved: dict[str, _T] = {}
    for peer_id, value in peer_mapping.items():
        canonical = canonicalize_peer_id(peer_id, configuration)
        existing = resolved.get(canonical)
        if existing is not None and existing != value:
            raise ValidationException(
                f"Conflicting values resolve to canonical identity {canonical!r}"
            )
        resolved[canonical] = value
    return resolved
