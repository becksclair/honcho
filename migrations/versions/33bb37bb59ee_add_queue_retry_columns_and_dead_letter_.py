"""add queue retry columns and dead letter archive

Adds durable retry state to the ``queue`` table and a permanent
``queue_dead_letter`` archive so exhausted representation tasks are archived
rather than silently dropped.

- ``queue.attempts`` (SmallInteger, not null, server default 0): per-item
  attempt counter. The server default makes the backfill free and leaves every
  existing row immediately eligible.
- ``queue.next_attempt_at`` (timestamptz, nullable): backoff timestamp honored
  by the poller. NULL means "eligible now".
- ``ix_queue_pending_next_attempt``: partial index over ``next_attempt_at``
  where ``processed = false`` to keep the poller's eligibility predicate cheap.
- ``queue_dead_letter``: FK-free archive of dead-lettered items, capturing
  everything needed to inspect and replay them.

Revision ID: 33bb37bb59ee
Revises: e4eba9cfaa6f
Create Date: 2026-07-08 22:14:36.505011

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from migrations.utils import (
    column_exists,
    get_schema,
    index_exists,
    table_exists,
)

# revision identifiers, used by Alembic.
revision: str = "33bb37bb59ee"
down_revision: str | None = "e4eba9cfaa6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

schema = get_schema()


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    # 1. Retry-state columns on the queue table. Server defaults keep the
    #    backfill free and make existing rows immediately eligible.
    if not column_exists("queue", "attempts", inspector):
        op.add_column(
            "queue",
            sa.Column(
                "attempts",
                sa.SmallInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            schema=schema,
        )

    if not column_exists("queue", "next_attempt_at", inspector):
        op.add_column(
            "queue",
            sa.Column(
                "next_attempt_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            schema=schema,
        )

    # 2. Permanent, FK-free dead-letter archive.
    if not table_exists("queue_dead_letter", inspector):
        op.create_table(
            "queue_dead_letter",
            sa.Column("id", sa.TEXT(), primary_key=True, nullable=False),
            sa.Column("original_queue_id", sa.BigInteger(), nullable=True),
            sa.Column("session_id", sa.TEXT(), nullable=True),
            sa.Column("work_unit_key", sa.TEXT(), nullable=False),
            sa.Column("task_type", sa.TEXT(), nullable=False),
            sa.Column("payload", JSONB, nullable=False),
            sa.Column("workspace_name", sa.TEXT(), nullable=True),
            sa.Column("message_id", sa.BigInteger(), nullable=True),
            sa.Column("attempts", sa.SmallInteger(), nullable=False),
            sa.Column("error", sa.TEXT(), nullable=True),
            sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "dead_lettered_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
            schema=schema,
        )

    # 3. Partial index to keep the poller's backoff-eligibility predicate cheap.
    if not index_exists("queue", "ix_queue_pending_next_attempt", inspector):
        op.create_index(
            "ix_queue_pending_next_attempt",
            "queue",
            ["next_attempt_at"],
            postgresql_where=sa.text("processed = false"),
            schema=schema,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if index_exists("queue", "ix_queue_pending_next_attempt", inspector):
        op.drop_index(
            "ix_queue_pending_next_attempt",
            table_name="queue",
            schema=schema,
        )

    if table_exists("queue_dead_letter", inspector):
        op.drop_table("queue_dead_letter", schema=schema)

    if column_exists("queue", "next_attempt_at", inspector):
        op.drop_column("queue", "next_attempt_at", schema=schema)

    if column_exists("queue", "attempts", inspector):
        op.drop_column("queue", "attempts", schema=schema)
