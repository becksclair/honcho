"""Integration tests for the deriver queue retry / dead-letter durability path.

These exercise the two acceptance scenarios from the ExecPlan
(``docs/plans/deriver-retry-durability.md``) by driving the failure handler
``QueueManager._handle_processing_error`` directly against a real per-test
database:

- Scenario A: a transient (retryable) failure DEFERS the head item with
  backoff, incrementing ``attempts`` and setting ``next_attempt_at`` in the
  future, storing the UNWRAPPED cause, and creating NO dead-letter row.
- Scenario B: a permanent (non-retryable) failure, and a persistently-transient
  failure that exhausts ``MAX_PROCESSING_ATTEMPTS``, both DEAD-LETTER: the
  source queue row is marked processed with the unwrapped error, exactly one
  ``queue_dead_letter`` row is archived, and a ``RepresentationDeadLetteredEvent``
  is emitted.
- Replay: ``src.deriver.dead_letter.replay_dead_letters`` re-enqueues a
  dead-lettered item as a fresh queue row and stamps the archive.

The failure handler and its ``defer_queue_item`` / ``dead_letter_queue_item``
collaborators use ``tracked_db`` internally; the top-level conftest patches
those import sites to the per-test engine, so state written by the handler is
visible via ``db_session`` after an ``expire_all()``.
"""

import datetime
from collections.abc import Callable
from typing import Any

import pytest
import tenacity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import models
from src.config import settings
from src.deriver import queue_manager as qm_module
from src.deriver.dead_letter import replay_dead_letters
from src.deriver.queue_manager import QueueManager
from src.exceptions import ValidationException
from src.telemetry.events.representation import RepresentationDeadLetteredEvent

pytestmark = pytest.mark.asyncio


def _wrap_retry_error(cause: BaseException) -> tenacity.RetryError:
    """Wrap ``cause`` in a ``tenacity.RetryError`` the way the LLM layer does.

    The stored ``queue.error`` must name the underlying cause, not the opaque
    ``RetryError[<Future ...>]`` wrapper, so the tests wrap causes this way to
    prove the handler unwraps them.
    """
    future = tenacity.Future(attempt_number=1)
    future.set_exception(cause)
    return tenacity.RetryError(future)


@pytest.fixture
async def representation_item(
    sample_session_with_peers: tuple[models.Session, list[models.Peer]],
    sample_messages: list[models.Message],
    create_queue_payload: Callable[..., Any],
    add_queue_items: Callable[..., Any],
) -> models.QueueItem:
    """A single unprocessed representation queue item with a valid work_unit_key."""
    session, peers = sample_session_with_peers
    peer = peers[0]
    message = sample_messages[0]
    payload = create_queue_payload(
        message=message,
        task_type="representation",
        observer=peer.name,
        observed=peer.name,
    )
    items = await add_queue_items(
        [(payload, message.id)], session.id, session.workspace_name
    )
    return items[0]


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture telemetry events emitted by the queue manager's failure path.

    The queue manager imports ``emit`` by name, so patch it at that import site.
    """
    events: list[Any] = []
    monkeypatch.setattr(qm_module, "emit", events.append)
    return events


async def _refetch(db_session: AsyncSession, item_id: int) -> models.QueueItem:
    db_session.expire_all()
    return (
        await db_session.execute(
            select(models.QueueItem).where(models.QueueItem.id == item_id)
        )
    ).scalar_one()


async def _dead_letters(db_session: AsyncSession) -> list[models.QueueDeadLetter]:
    db_session.expire_all()
    return list(
        (await db_session.execute(select(models.QueueDeadLetter))).scalars().all()
    )


class TestTransientFailureDefers:
    """Scenario A — a transient failure recovers with zero loss (defer, no drop)."""

    async def test_transient_failure_defers_with_unwrapped_error(
        self,
        db_session: AsyncSession,
        representation_item: models.QueueItem,
        captured_events: list[Any],
    ) -> None:
        item = representation_item
        work_unit_key = item.work_unit_key
        item_id = item.id
        qm = QueueManager()

        cause = ValidationException("provider returned incomplete response")
        error = _wrap_retry_error(cause)

        before = datetime.datetime.now(datetime.timezone.utc)
        await qm._handle_processing_error(  # pyright: ignore[reportPrivateUsage]
            error, [item], work_unit_key, "processing representation batch"
        )

        refreshed = await _refetch(db_session, item_id)

        # Item stays pending, first attempt recorded, backoff scheduled ahead.
        assert refreshed.processed is False
        assert refreshed.attempts == 1
        assert refreshed.next_attempt_at is not None
        assert refreshed.next_attempt_at > before

        # Durable error names the unwrapped cause, not the RetryError wrapper.
        assert refreshed.error is not None
        assert "RetryError" not in refreshed.error
        assert refreshed.error.startswith("ValidationException")
        assert "provider returned incomplete response" in refreshed.error

        # Nothing archived, no dead-letter alert.
        assert await _dead_letters(db_session) == []
        assert not any(
            isinstance(e, RepresentationDeadLetteredEvent) for e in captured_events
        )


class TestPermanentFailureDeadLetters:
    """Scenario B — permanent failures are archived and alerted, not dropped."""

    async def test_non_retryable_dead_letters_on_first_attempt(
        self,
        db_session: AsyncSession,
        representation_item: models.QueueItem,
        captured_events: list[Any],
    ) -> None:
        item = representation_item
        work_unit_key = item.work_unit_key
        item_id = item.id
        original_payload = dict(item.payload)
        workspace_name = item.workspace_name
        qm = QueueManager()

        # "maximum token limit" is on the retry-policy deny-list -> dead-letter now.
        cause = ValueError(
            "Observation content exceeds maximum token limit of 2000 tokens"
        )
        error = _wrap_retry_error(cause)

        await qm._handle_processing_error(  # pyright: ignore[reportPrivateUsage]
            error, [item], work_unit_key, "processing representation batch"
        )

        # Source queue row: exactly one attempt, marked processed, unwrapped error.
        refreshed = await _refetch(db_session, item_id)
        assert refreshed.processed is True
        assert refreshed.attempts == 1
        assert refreshed.error is not None
        assert "RetryError" not in refreshed.error
        assert refreshed.error.startswith("ValueError")

        # Exactly one archive row, payload copied, attempts=1, unwrapped error.
        dl_rows = await _dead_letters(db_session)
        assert len(dl_rows) == 1
        dl = dl_rows[0]
        assert dl.attempts == 1
        assert dl.original_queue_id == item_id
        assert dl.payload == original_payload
        assert dl.error is not None
        assert "RetryError" not in dl.error
        assert "maximum token limit" in dl.error
        assert dl.replayed_at is None

        # Exactly one dead-letter alert emitted for a representation task.
        dl_events = [
            e for e in captured_events if isinstance(e, RepresentationDeadLetteredEvent)
        ]
        assert len(dl_events) == 1
        assert dl_events[0].attempts == 1
        assert dl_events[0].workspace_name == workspace_name
        assert "RetryError" not in dl_events[0].error

    async def test_persistent_transient_dead_letters_at_max_attempts(
        self,
        db_session: AsyncSession,
        representation_item: models.QueueItem,
        captured_events: list[Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Shrink the attempt budget so the test does not walk all 10 defaults.
        monkeypatch.setattr(settings.DERIVER, "MAX_PROCESSING_ATTEMPTS", 3)

        item = representation_item
        work_unit_key = item.work_unit_key
        item_id = item.id
        qm = QueueManager()

        # Retryable cause (not on the deny-list): defers until the budget is hit.
        cause = RuntimeError("provider 503 service unavailable")
        error = _wrap_retry_error(cause)

        # Attempts 1 and 2 defer (attempts < MAX): still pending, no archive.
        for expected_attempts in (1, 2):
            current = await _refetch(db_session, item_id)
            await qm._handle_processing_error(  # pyright: ignore[reportPrivateUsage]
                error, [current], work_unit_key, "processing representation batch"
            )
            refreshed = await _refetch(db_session, item_id)
            assert refreshed.processed is False
            assert refreshed.attempts == expected_attempts
            assert refreshed.next_attempt_at is not None
            assert await _dead_letters(db_session) == []

        # Attempt 3 reaches MAX_PROCESSING_ATTEMPTS -> dead-letter.
        current = await _refetch(db_session, item_id)
        await qm._handle_processing_error(  # pyright: ignore[reportPrivateUsage]
            error, [current], work_unit_key, "processing representation batch"
        )

        refreshed = await _refetch(db_session, item_id)
        assert refreshed.processed is True
        assert refreshed.attempts == 3

        dl_rows = await _dead_letters(db_session)
        assert len(dl_rows) == 1
        assert dl_rows[0].attempts == 3
        assert dl_rows[0].error is not None
        assert "503 service unavailable" in dl_rows[0].error

        dl_events = [
            e for e in captured_events if isinstance(e, RepresentationDeadLetteredEvent)
        ]
        assert len(dl_events) == 1
        assert dl_events[0].attempts == 3


class TestReplayDeadLetters:
    """Manual replay re-enqueues an archived item as fresh queue work."""

    async def test_replay_re_enqueues_and_stamps_archive(
        self,
        db_session: AsyncSession,
        representation_item: models.QueueItem,
        captured_events: list[Any],  # noqa: ARG002  (patches emit to a no-op sink)
    ) -> None:
        item = representation_item
        work_unit_key = item.work_unit_key
        item_id = item.id
        original_payload = dict(item.payload)
        workspace_name = item.workspace_name
        qm = QueueManager()

        cause = ValueError("request exceeds maximum token limit")
        await qm._handle_processing_error(  # pyright: ignore[reportPrivateUsage]
            _wrap_retry_error(cause),
            [item],
            work_unit_key,
            "processing representation batch",
        )

        # Precondition: exactly one archived row, source row processed.
        dl_rows = await _dead_letters(db_session)
        assert len(dl_rows) == 1
        assert (await _refetch(db_session, item_id)).processed is True

        count = await replay_dead_letters(workspace_name=workspace_name)
        assert count == 1

        # A fresh, pristine queue row is enqueued (the source row is processed).
        db_session.expire_all()
        pending = list(
            (
                await db_session.execute(
                    select(models.QueueItem).where(
                        ~models.QueueItem.processed  # pyright: ignore[reportGeneralTypeIssues]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(pending) == 1
        fresh = pending[0]
        assert fresh.id != item_id
        assert fresh.attempts == 0
        assert fresh.next_attempt_at is None
        assert fresh.error is None
        assert fresh.work_unit_key == work_unit_key
        assert fresh.payload == original_payload

        # Archive row is stamped so a second replay will not double-enqueue it.
        dl_after = await _dead_letters(db_session)
        assert len(dl_after) == 1
        assert dl_after[0].replayed_at is not None

        # Idempotence: replaying again finds nothing new.
        assert await replay_dead_letters(workspace_name=workspace_name) == 0
