import logging
from collections.abc import Sequence

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi_pagination import Page
from fastapi_pagination.ext.sqlalchemy import apaginate
from sqlalchemy.ext.asyncio import AsyncSession

from src import crud, models, schemas
from src.dependencies import db, read_db
from src.exceptions import ResourceNotFoundException, ValidationException
from src.identity import resolve_peer_id
from src.security import require_auth
from src.telemetry.events import EmbeddingCallPurpose
from src.utils.types import embedding_call_purpose

logger = logging.getLogger(__name__)


def _serialize_conclusion(document: models.Document) -> schemas.Conclusion:
    """Build the public conclusion contract with both level field names."""
    return schemas.Conclusion(
        id=document.id,
        content=document.content,
        observer=document.observer,
        observed=document.observed,
        session_name=document.session_name,
        level=document.level,
        conclusion_type=document.level,
        created_at=document.created_at,
    )


def _serialize_conclusions(
    documents: Sequence[models.Document],
) -> list[schemas.Conclusion]:
    return [_serialize_conclusion(document) for document in documents]


async def _serialize_conclusions_for_pagination(
    documents: Sequence[models.Document],
) -> list[schemas.Conclusion]:
    return _serialize_conclusions(documents)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/conclusions",
    tags=["conclusions"],
    dependencies=[Depends(require_auth(workspace_name="workspace_id"))],
)


@router.post(
    "",
    response_model=list[schemas.Conclusion],
    status_code=201,
)
async def create_conclusions(
    workspace_id: str = Path(...),
    body: schemas.ConclusionBatchCreate = Body(
        ...,
        description="Batch of Conclusions to create",
    ),
    db: AsyncSession = db,
) -> list[schemas.Conclusion]:
    """
    Create one or more Conclusions.

    Conclusions are logical certainties derived from interactions between Peers. They form the basis of a Peer's Representation.
    """
    conclusions = [
        conclusion.model_copy(
            update={
                "observer_id": await resolve_peer_id(
                    db, workspace_id, conclusion.observer_id
                ),
                "observed_id": await resolve_peer_id(
                    db, workspace_id, conclusion.observed_id
                ),
            }
        )
        for conclusion in body.conclusions
    ]
    documents = await crud.create_observations(
        db,
        observations=conclusions,
        workspace_name=workspace_id,
    )

    logger.debug(
        "Created %d conclusions in workspace %s",
        len(documents),
        workspace_id,
    )
    return _serialize_conclusions(documents)


@router.post(
    "/list",
    response_model=Page[schemas.Conclusion],
)
async def list_conclusions(
    workspace_id: str = Path(...),
    options: schemas.ConclusionGet | None = Body(
        None,
        description="Filtering options for the Conclusions list",
    ),
    reverse: bool | None = Query(
        False,
        description="Whether to reverse the order of results",
    ),
    db: AsyncSession = read_db,
):
    """
    List Conclusions using optional filters, ordered by recency unless `reverse` is true. Results are paginated.
    """
    filters = None
    if options and hasattr(options, "filters"):
        filters = options.filters
        if filters == {}:
            filters = None

    if filters:
        filters = filters.copy()
        for key in ("observer", "observer_id"):
            if key in filters:
                filters[key] = await resolve_peer_id(db, workspace_id, filters[key])
        for key in ("observed", "observed_id"):
            if key in filters:
                filters[key] = await resolve_peer_id(db, workspace_id, filters[key])

    stmt = crud.get_documents_with_filters(
        workspace_name=workspace_id,
        filters=filters,
        reverse=reverse or False,
    )

    return await apaginate(
        db,
        stmt,
        transformer=_serialize_conclusions_for_pagination,
    )


@router.post(
    "/query",
    response_model=list[schemas.Conclusion],
)
async def query_conclusions(
    workspace_id: str = Path(...),
    body: schemas.ConclusionQuery = Body(
        ...,
        description="Semantic search parameters for Conclusions",
    ),
    db: AsyncSession = read_db,
) -> list[schemas.Conclusion]:
    """
    Query Conclusions using semantic search. Use `top_k` to control the number of results returned.
    """
    observer = None
    observed = None
    if body.filters:
        observer = body.filters.get("observer") or body.filters.get("observer_id")
        observed = body.filters.get("observed") or body.filters.get("observed_id")

    if not observer or not observed:
        raise ValidationException(
            "observer and observed must be specified for semantic search"
        )

    observer = await resolve_peer_id(db, workspace_id, observer)
    observed = await resolve_peer_id(db, workspace_id, observed)
    filters = (body.filters or {}).copy()
    filters.pop("observer_id", None)
    filters.pop("observed_id", None)
    filters["observer"] = observer
    filters["observed"] = observed

    with embedding_call_purpose(
        EmbeddingCallPurpose.GENERIC_DOCUMENT_SEARCH.value,
        workspace_name=workspace_id,
        parent_category="api",
    ):
        documents = await crud.query_documents(
            db,
            workspace_name=workspace_id,
            query=body.query,
            observer=observer,
            observed=observed,
            filters=filters,
            max_distance=body.distance,
            top_k=body.top_k,
        )
    return _serialize_conclusions(list(documents))


@router.delete(
    "/{conclusion_id}",
    status_code=204,
    response_model=None,
)
async def delete_conclusion(
    workspace_id: str = Path(...),
    conclusion_id: str = Path(...),
    db: AsyncSession = db,
):
    """
    Delete a single Conclusion by ID.

    This action cannot be undone.
    """
    try:
        await crud.delete_document_by_id(
            db,
            workspace_name=workspace_id,
            document_id=conclusion_id,
        )

        logger.debug("Conclusion %s deleted successfully", conclusion_id)
    except ResourceNotFoundException:
        raise
    except ValueError as e:
        logger.warning(f"Failed to delete conclusion {conclusion_id}: {str(e)}")
        raise ResourceNotFoundException("Conclusion not found") from e
