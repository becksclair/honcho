"""Manual replay operation for the deriver dead-letter archive.

When a representation (or any other) task exhausts its retry budget or hits a
deterministic failure, it is archived to the ``queue_dead_letter`` table instead
of being silently dropped (see ``QueueManager.dead_letter_queue_item`` in
``src.deriver.queue_manager``). This module provides the operator-facing
counterpart: re-enqueue archived tasks into the working ``queue`` so the deriver
processes them again.

Replay is safe to run repeatedly. Only rows that have not yet been replayed
(``replayed_at IS NULL``) are selected, and each replayed row is stamped so a
subsequent invocation will not double-enqueue it. Because the deriver
deduplicates conclusions, even an accidental double replay does not create
duplicate memory.

Example invocation::

    uv run python -c "import asyncio; \
from src.deriver.dead_letter import replay_dead_letters; \
print(asyncio.run(replay_dead_letters(workspace_name='openclaw')))"
"""

import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from src import models
from src.dependencies import tracked_db

logger = logging.getLogger(__name__)

__all__ = ["replay_dead_letters"]


async def replay_dead_letters(
    workspace_name: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> int:
    """Re-enqueue archived dead-letter rows into the working queue.

    Selects ``queue_dead_letter`` rows that have not yet been replayed
    (``replayed_at IS NULL``), optionally narrowed by workspace, an
    ``dead_lettered_at`` lower bound, and a maximum count. For each match a fresh
    ``queue`` row is inserted (``processed=False``, ``attempts=0``,
    ``next_attempt_at=None``, ``error=None``) copying the archived
    ``payload``/``work_unit_key``/``task_type``/``workspace_name``/``message_id``/
    ``session_id``; the archive row is then stamped ``replayed_at=now()`` so it is
    not replayed again.

    Args:
        workspace_name: If given, only replay archive rows for this workspace.
        since: If given, only replay rows dead-lettered at or after this
            timezone-aware timestamp.
        limit: If given, replay at most this many rows (oldest first).

    Returns:
        The number of archive rows re-enqueued.
    """
    async with tracked_db("replay_dead_letters") as db:
        query = select(models.QueueDeadLetter).where(
            models.QueueDeadLetter.replayed_at.is_(None)
        )
        if workspace_name is not None:
            query = query.where(models.QueueDeadLetter.workspace_name == workspace_name)
        if since is not None:
            query = query.where(models.QueueDeadLetter.dead_lettered_at >= since)
        query = query.order_by(models.QueueDeadLetter.dead_lettered_at.asc())
        if limit is not None:
            query = query.limit(limit)

        rows = (await db.execute(query)).scalars().all()
        if not rows:
            return 0

        replayed = 0
        for row in rows:
            # `dream`/`reconciler` tasks carry a stable, recurring work_unit_key
            # and the queue enforces a partial unique index on pending rows for
            # those types (uq_queue_{dream,reconciler}_pending_work_unit_key). If
            # a pending row for the same key already exists, the fresh insert
            # would violate it. ON CONFLICT DO NOTHING skips that row instead of
            # aborting the whole transaction (which would strand the entire
            # batch) — the pending row already covers the work, so it is safe.
            result = await db.execute(
                pg_insert(models.QueueItem)
                .values(
                    session_id=row.session_id,
                    work_unit_key=row.work_unit_key,
                    task_type=row.task_type,
                    payload=row.payload,
                    processed=False,
                    error=None,
                    workspace_name=row.workspace_name,
                    message_id=row.message_id,
                    attempts=0,
                    next_attempt_at=None,
                )
                .on_conflict_do_nothing()
                .returning(models.QueueItem.id)
            )
            if result.scalar_one_or_none() is not None:
                replayed += 1
            # Stamp every selected archive row regardless of conflict outcome so a
            # permanently-colliding key is not retried forever on each invocation.
            await db.execute(
                update(models.QueueDeadLetter)
                .where(models.QueueDeadLetter.id == row.id)
                .values(replayed_at=func.now())
            )

        await db.commit()

        logger.info(
            "Replayed %d of %d dead-letter row(s) (workspace=%s, since=%s, limit=%s)",
            replayed,
            len(rows),
            workspace_name,
            since,
            limit,
        )
        return replayed
