"""Hooks for revision 33bb37bb59ee (add_queue_retry_columns_and_dead_letter_)."""

from __future__ import annotations

from sqlalchemy import text

from tests.alembic.registry import register_after_upgrade, register_before_upgrade
from tests.alembic.verifier import MigrationVerifier


@register_before_upgrade("33bb37bb59ee")
def prepare_add_queue_retry_columns_and_dead_letter(
    verifier: MigrationVerifier,
) -> None:
    """Seed state and assert the new objects are absent before upgrade."""
    # New queue columns must not yet exist.
    verifier.assert_column_exists("queue", "attempts", exists=False)
    verifier.assert_column_exists("queue", "next_attempt_at", exists=False)

    # The archive table and partial index must not yet exist.
    verifier.assert_table_exists("queue_dead_letter", exists=False)
    verifier.assert_indexes_not_exist([("queue", "ix_queue_pending_next_attempt")])

    # Seed a queue row so we can confirm the server default backfills it.
    verifier.conn.execute(
        text(
            f'INSERT INTO "{verifier.schema}"."queue" '
            + '("work_unit_key", "task_type", "payload") '
            + "VALUES (:work_unit_key, :task_type, :payload)"
        ),
        {
            "work_unit_key": "representation:ws:sess:observer",
            "task_type": "representation",
            "payload": "{}",
        },
    )


@register_after_upgrade("33bb37bb59ee")
def verify_add_queue_retry_columns_and_dead_letter(
    verifier: MigrationVerifier,
) -> None:
    """Validate the retry columns, archive table, and partial index."""
    # Queue retry columns exist with the expected nullability.
    verifier.assert_column_exists("queue", "attempts", nullable=False)
    verifier.assert_column_exists("queue", "next_attempt_at", nullable=True)

    # Server default backfilled the pre-existing row to attempts = 0 and left
    # next_attempt_at NULL (immediately eligible). No NULL attempts anywhere.
    verifier.assert_no_nulls("queue", "attempts")
    row = verifier.conn.execute(
        text(
            f'SELECT "attempts", "next_attempt_at" FROM "{verifier.schema}"."queue" '
            + "WHERE work_unit_key = :work_unit_key"
        ),
        {"work_unit_key": "representation:ws:sess:observer"},
    ).one()
    assert row.attempts == 0
    assert row.next_attempt_at is None

    # Partial index exists.
    verifier.assert_indexes_exist([("queue", "ix_queue_pending_next_attempt")])

    # Dead-letter archive table exists with the full column set.
    verifier.assert_table_exists("queue_dead_letter")
    verifier.assert_column_exists("queue_dead_letter", "id", nullable=False)
    verifier.assert_column_exists(
        "queue_dead_letter", "original_queue_id", nullable=True
    )
    verifier.assert_column_exists("queue_dead_letter", "session_id", nullable=True)
    verifier.assert_column_exists("queue_dead_letter", "work_unit_key", nullable=False)
    verifier.assert_column_exists("queue_dead_letter", "task_type", nullable=False)
    verifier.assert_column_exists("queue_dead_letter", "payload", nullable=False)
    verifier.assert_column_exists("queue_dead_letter", "workspace_name", nullable=True)
    verifier.assert_column_exists("queue_dead_letter", "message_id", nullable=True)
    verifier.assert_column_exists("queue_dead_letter", "attempts", nullable=False)
    verifier.assert_column_exists("queue_dead_letter", "error", nullable=True)
    verifier.assert_column_exists(
        "queue_dead_letter", "first_failed_at", nullable=True
    )
    verifier.assert_column_exists(
        "queue_dead_letter", "dead_lettered_at", nullable=False
    )
    verifier.assert_column_exists("queue_dead_letter", "replayed_at", nullable=True)

    # An archive row can be inserted with no FK to any live queue/session/message
    # row, and dead_lettered_at is server-defaulted.
    verifier.conn.execute(
        text(
            f'INSERT INTO "{verifier.schema}"."queue_dead_letter" '
            + '("id", "original_queue_id", "work_unit_key", "task_type", '
            + '"payload", "attempts") '
            + "VALUES (:id, :original_queue_id, :work_unit_key, :task_type, "
            + ":payload, :attempts)"
        ),
        {
            "id": "dl_test_row",
            "original_queue_id": 999999,
            "work_unit_key": "representation:ws:sess:observer",
            "task_type": "representation",
            "payload": "{}",
            "attempts": 10,
        },
    )
    archived = verifier.conn.execute(
        text(
            'SELECT "attempts", "dead_lettered_at", "replayed_at" '
            + f'FROM "{verifier.schema}"."queue_dead_letter" WHERE id = :id'
        ),
        {"id": "dl_test_row"},
    ).one()
    assert archived.attempts == 10
    assert archived.dead_lettered_at is not None
    assert archived.replayed_at is None
