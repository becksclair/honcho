import datetime
import logging
from typing import cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.dependencies import read_db
from src.exceptions import DisabledException, ValidationException
from src.identity import resolve_peer_id
from src.security import (
    JWTParams,
    create_jwt,
    require_auth,
    scope_requires_workspace,
)
from src.utils.formatting import format_datetime_utc

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/keys",
    tags=["keys"],
    dependencies=[Depends(require_auth(admin=True))],
)


@router.post("")
async def create_key(
    workspace_id: str | None = Query(
        None, description="ID of the workspace to scope the key to"
    ),
    peer_id: str | None = Query(None, description="ID of the peer to scope the key to"),
    session_id: str | None = Query(
        None, description="ID of the session to scope the key to"
    ),
    expires_at: datetime.datetime | None = None,
    db: AsyncSession = read_db,
):
    """Create a new Key"""
    if not settings.AUTH.USE_AUTH:
        raise DisabledException()

    # Validate that at least one parameter is provided for proper scoping
    if not any([workspace_id, peer_id, session_id]):
        raise ValidationException(
            "At least one of workspace_id, peer_id, or session_id must be provided"
        )

    # A peer- or session-scoped key must carry its parent workspace, otherwise
    # verify_jwt rejects it on every request (the workspace is required to rule
    # out cross-workspace use). Shares the predicate with verify_jwt so the
    # creation-time guard and the verification-time invariant cannot drift.
    if scope_requires_workspace(
        peer=peer_id, session=session_id, workspace=workspace_id
    ):
        raise ValidationException(
            "workspace_id is required when scoping a key to a peer or session"
        )

    db_candidate = cast(object, db)
    if (
        workspace_id is not None
        and peer_id is not None
        and isinstance(db_candidate, AsyncSession)
    ):
        peer_id = await resolve_peer_id(db_candidate, workspace_id, peer_id)

    key_str = create_jwt(
        JWTParams(
            exp=format_datetime_utc(expires_at) if expires_at else None,
            w=workspace_id,
            p=peer_id,
            s=session_id,
        )
    )
    return {
        "key": key_str,
    }
