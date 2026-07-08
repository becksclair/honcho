# Make deriver representation failures durable: bounded retry, backoff, and a permanent dead-letter archive

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository checks in a plan-authoring standard at `~/.agents/PLANS.md` (outside the repo tree, in the operator's home). This document must be maintained in accordance with that standard: keep it fully self-contained, revise it as work proceeds, and record every decision in the Decision Log with its rationale.


## Purpose / Big Picture

Honcho forms memory in a background worker called the **deriver**. For every message (or small batch of messages), the deriver makes a single large-language-model (LLM) call that extracts "conclusions" (facts and inferences about a peer) and stores them. That unit of work is a **representation task**.

Today, when a representation task's LLM call fails and its in-call retries are exhausted, the deriver marks the work as done and records the error string, then moves on. There is no second attempt later and no durable record of what was lost. The failed message's memory is silently dropped forever. On 2026-07-03 a multi-hour instability window at the LLM provider caused 484 representation tasks to be dropped this way in a single workspace; a smaller wave dropped 66 more on 2026-07-08. Nobody was alerted; the loss was discovered only by manually inspecting the queue table five days later.

After this change, a transient LLM failure no longer destroys memory. A failed representation task is retried later with exponential backoff, up to a bounded number of attempts spanning several hours. If it still cannot succeed (a genuinely "poisonous" message, or an outage longer than the retry window), the task is moved to a permanent **dead-letter archive** table instead of being silently discarded, and an alert event is emitted. Operators can inspect the archive and replay its entries on demand.

You will be able to see this working in three concrete ways:

- Start the stack, force a representation task to fail transiently a few times, and observe that the item is retried and eventually succeeds with no lost conclusions.
- Force a representation task to fail permanently and observe that after the configured maximum attempts a row appears in a new `queue_dead_letter` table, a `representation.dead_lettered` telemetry event is emitted, and the working queue no longer contains the item as pending.
- Run the test suite and see new tests that fail before this change and pass after it.


## Progress

Use timestamps (UTC) to measure rate of progress. Every stopping point must be reflected here.

- [x] (2026-07-08 ~22:20Z) Milestone 1 — `src/deriver/retry_policy.py` (`unwrap_retry_error`, `format_error`, `is_retryable`, `compute_backoff`) + `tests/deriver/test_retry_policy.py`. Logic verified by direct import assertions (host pytest could not run these; see Surprises).
- [x] (2026-07-08 ~22:30Z) Milestone 2 — Schema: `queue.attempts` (SmallInteger, default 0) + `queue.next_attempt_at` (timestamptz), partial index `ix_queue_pending_next_attempt`, and the FK-free `queue_dead_letter` archive table; Alembic migration `33bb37bb59ee` plus its required `tests/alembic/revisions/` test.
- [x] (2026-07-08 ~23:00Z) Milestone 3 — Retry engine: `_handle_processing_error` unwraps the cause and routes to `defer_queue_item` (backoff) or `dead_letter_queue_item` (archive + mark processed); `next_attempt_at` eligibility added via `_queue_item_eligible_now()` to `get_and_claim_work_units`, `get_queue_item_batch`, and `get_next_queue_item`.
- [x] (2026-07-08 ~22:54Z) Milestone 4 — `RepresentationDeadLetteredEvent` telemetry + Sentry capture on dead-letter. Prometheus counters intentionally deferred (optional; no existing retry/dead-letter counter to extend).
- [x] (2026-07-08 ~22:54Z) Milestone 5 — `DERIVER.MAX_PROCESSING_ATTEMPTS=10`, `RETRY_BACKOFF_BASE_SECONDS=30.0`, `RETRY_BACKOFF_MAX_SECONDS=7200.0`, `RETRY_BACKOFF_JITTER=0.2` in `src/config.py`, documented under `[deriver]` in `config.toml`.
- [x] (2026-07-08 ~23:00Z) Milestone 6 — `src/deriver/dead_letter.py::replay_dead_letters`. Hardened during review with `ON CONFLICT DO NOTHING` so a dead-lettered `dream`/`reconciler` task cannot abort the batch on the pending partial-unique index (see Decision Log).
- [x] (2026-07-08 ~21:37Z) Milestone 7 — `tests/deriver/test_queue_retry.py` (4 scenarios: transient-defers, non-retryable-dead-letters, max-attempts-dead-letters, replay) pass 4/4; migration applied to the live DB; deriver + api images rebuilt and healthy; ultra-review pass run with fixes applied and verified.


## Surprises & Discoveries

Document unexpected behaviors and insights as you go, with concise evidence.

- Observation: The current failure path only marks the FIRST item of a failed batch as errored+processed; the rest stay pending and are retried on the next loop. During a sustained outage this walks the batch one head-item at a time, dropping each in turn. This is why an outage produces many individually-dropped items rather than one.
  Evidence: `src/deriver/queue_manager.py` `_handle_processing_error` (around line 574) calls `mark_queue_item_as_errored(items[0], ...)`; the docstring states "We only mark the first queue item as errored so we don't potentially throw away a batch."

- Observation: The stored `error` for these failures is the useless outer string `RetryError[<Future ... raised ValidationException>]`; the underlying cause is discarded. The full traceback IS written to the deriver logs via `logger.error(..., exc_info=True)`, but the durable `queue.error` column is not.
  Evidence: A production sample stored exactly `RetryError: RetryError[<Future at 0x... state=finished raised ValidationException>]`; the deriver logs contained the real cause.

- Observation: Successful queue items are deleted almost immediately by the cleanup sweep, while errored items are retained for `QUEUE_ERROR_RETENTION_SECONDS` (default 30 days). This is why the dropped items were still inspectable days later.
  Evidence: `src/reconciler/queue_cleanup.py` `cleanup_queue_items` deletes processed items where `error IS NULL` unconditionally and where `error IS NOT NULL` only after the retention cutoff; `src/config.py` sets `QUEUE_ERROR_RETENTION_SECONDS = 30 * 24 * 3600`.

- Observation: Retries do not need a new scheduler. The deriver's polling loop re-evaluates work-unit eligibility every cycle, so an item whose retry time is in the future simply becomes eligible again once that time passes.
  Evidence: `src/deriver/queue_manager.py` `get_and_claim_work_units` selects work units purely from current queue state on each poll.

- Observation: The deriver session (`SessionLocal` in `src/db.py`) is configured `expire_on_commit=False`. This means a `QueueItem` handed to `_handle_processing_error` from a since-closed batch session keeps its loaded attribute values, so reading `item.attempts`/`item.payload` there does NOT trigger a lazy load. An early worry that the failure path would raise `sqlalchemy.exc.MissingGreenlet` in production was therefore a false positive; the `MissingGreenlet` seen during testing was purely a test-session (conftest) artifact.
  Evidence: `src/db.py` `SessionLocal = async_sessionmaker(..., expire_on_commit=False)`; the production `MissingGreenlet` did not reproduce, and the failing test assertion read an expired attribute in the test's own session.

- Observation: Host-side `uv run pytest` wedges in this repo. The pytest `addopts` include `-n auto` (pytest-xdist) and `tests/conftest.py` provisions a per-session test database with DB-coupled autouse fixtures, but the Postgres DB is only reachable inside the Docker network. Pure unit tests must be run either with `-o addopts="" --noconftest` or by exercising the functions via a direct `uv run python` import; DB-backed tests run with the DB reached at the container bridge IP and `-o addopts=""` to disable xdist.
  Evidence: A `test_retry_policy.py` run hung ~18 minutes on collection; `pyproject.toml` sets `addopts = "--strict-markers -n auto --ignore=tests/alembic"`.

- Observation: `dream` and `reconciler` tasks can be dead-lettered (they reach `_handle_processing_error` via the single-item `get_next_queue_item` → `process_item` path) and carry stable, recurring `work_unit_key`s guarded by partial unique indexes on pending rows. A naive per-row replay therefore risks an `IntegrityError` that aborts the whole replay batch. Surfaced by the ultra-review correctness lane; fixed with `ON CONFLICT DO NOTHING`.
  Evidence: `src/models.py` `uq_queue_dream_pending_work_unit_key` / `uq_queue_reconciler_pending_work_unit_key`; `replay_dead_letters` originally used a plain `insert`.


## Decision Log

- Decision: Implement retries as a per-item attempt counter plus a `next_attempt_at` backoff timestamp, honored by the existing polling loop, rather than adding a new reconciler task or an external scheduler.
  Rationale: The poll loop already provides re-eligibility for free. A backoff timestamp turns "drop the head" into "defer the head" with no new moving parts. Minimal machinery, minimal risk.
  Date/Author: 2026-07-08, Sky (design).

- Decision: Keep the per-item (items[0]) granularity of failure handling instead of deferring the whole batch.
  Rationale: Per-item deferral preserves the existing poison-isolation property (a single bad message does not nuke the batch) while also surviving transient outages (each item gets its own bounded retry schedule). Batch-level deferral is noted as a possible future optimization but is out of scope for v1 because it complicates the isolation guarantee.
  Date/Author: 2026-07-08, Sky (design).

- Decision: Set maximum attempts to 10 with exponential backoff base 30s and cap 7200s (2 hours), giving roughly a four-hour total retry window before dead-lettering.
  Rationale: The 2026-07-03 incident was a multi-hour elevated-error window. A four-hour survival window rides out an incident of that class without dead-lettering healthy items. The cost is longer queue dwell for items during an outage, which is acceptable.
  Date/Author: 2026-07-08, Sky (per operator direction to survive multi-hour outages).

- Decision: Dead-letters go into a dedicated permanent table `queue_dead_letter`, not left as errored rows in `queue`.
  Rationale: Errored `queue` rows are auto-deleted after the 30-day retention window, so leaving dead-letters there means eventual silent loss. In a memory system, each dropped item is lost cognition; the archive must be permanent and independent of queue retention. It also becomes the clean source for manual replay.
  Date/Author: 2026-07-08, Sky (per operator direction for a permanent archive).

- Decision: Add a lightweight `is_retryable(exception)` classifier. Deterministic, configuration-shaped failures (for example "codex transport does not support stop sequences", "Unsupported Codex tool_choice shape", input-token-cap violations) are dead-lettered on first failure; everything else, including unknown errors, is treated as transient and retried.
  Rationale: Retrying a deterministic failure ten times wastes money and delays the inevitable dead-letter. Defaulting unknowns to "retryable" is the safe choice for a durability feature: better to retry than to lose memory.
  Date/Author: 2026-07-08, Sky (design).

- Decision: `dead_letter_queue_item` archives via `INSERT ... SELECT` from the live `queue` row rather than reading the ORM instance's attributes.
  Rationale: A current-row SQL snapshot is independent of session `expire_on_commit` semantics and correct whether or not the `QueueItem` is still session-attached. Given `expire_on_commit=False` the simpler `.values(item.*)` would also work, but the SQL copy is strictly more robust at no cost.
  Date/Author: 2026-07-08, Sky (implementation; comment corrected during review after the `MissingGreenlet` rationale was found to be false).

- Decision: `replay_dead_letters` uses `ON CONFLICT DO NOTHING` and stamps `replayed_at` on every selected archive row regardless of conflict outcome.
  Rationale: `dream`/`reconciler` dead-letters carry recurring `work_unit_key`s under partial unique indexes on pending rows; a plain insert would raise `IntegrityError` and abort the whole replay transaction, permanently stranding the batch. Skipping the conflicting row is safe because a pending row already covers the work, and stamping regardless prevents a permanently-colliding key from being retried forever. The returned count reflects rows actually re-enqueued.
  Date/Author: 2026-07-08, Sky (fix from ultra-review correctness lane).

- Decision: Accepted three review residuals as advisory rather than fixing them. (1) Out-of-order representation ingestion after a deferral is acceptable — it is strictly better than the prior behavior, which dropped the head entirely, and blocking the session on a deferred head would reintroduce head-of-line stalls. (2) `first_failed_at` stays NULL — populating it honestly would require a new `queue` column and migration, disproportionate to the forensic value. (3) `queue_dead_letter` is intentionally never pruned (permanent archive) and its low fill rate does not justify an extra index yet.
  Rationale: None blocks correctness or the stated purpose; pruning in particular would contradict the permanent-archive decision above.
  Date/Author: 2026-07-08, Sky (ultra-review arbitration; operator-accepted).


## Outcomes & Retrospective

Delivered and live as of 2026-07-08. The deriver no longer silently drops a representation task on failure. A transient failure now defers with exponential backoff (base 30s, cap 2h, up to 10 attempts — roughly a four-hour window) and is retried by the existing poll loop once `next_attempt_at` passes. A deterministic/non-retryable failure, or one that exhausts the attempt budget, is archived to the permanent `queue_dead_letter` table, the source row is marked processed, and a `representation.dead_lettered` event is emitted — instead of the memory vanishing. Operators can re-enqueue archived tasks with `replay_dead_letters`.

Against the Purpose: the two acceptance scenarios are demonstrated by `tests/deriver/test_queue_retry.py` (4/4 pass) — transient recovers with zero lost conclusions, and a permanent failure yields exactly one archive row plus a dead-letter event with the source row marked processed. Migration `33bb37bb59ee` is applied to the live database and both the deriver and api containers boot clean.

Verification and review: unit logic verified by direct import assertions; integration scenarios verified against a real Postgres; ruff and basedpyright clean. A three-lane ultra-review (correctness, simplify/thermo, performance) plus a focused delta re-review followed. The `autoreview` helper flaked (structured-output retry failure) and was replaced by an independent Opus correctness reviewer lane. One real bug was found and fixed — `replay_dead_letters` aborting its whole batch on the `dream`/`reconciler` pending unique index — along with two cosmetic cleanups (a no-op error truncation and a misleading comment). The delta re-review returned no findings.

Accepted residuals (see Decision Log): out-of-order ingestion after a deferral, `first_failed_at` left NULL, and no archive index or pruning. Low-urgency follow-ups worth doing: a dedicated regression test for the `dream`/`reconciler` replay-collision path, and Prometheus retry/dead-letter counters.

Lessons: (1) `SessionLocal`'s `expire_on_commit=False` is what makes the detached-`QueueItem` reads in the failure path safe — know this before touching that code. (2) Host `uv run pytest` wedges here (`-n auto` xdist + Docker-only DB); run DB-backed tests against the container bridge IP with `-o addopts=""`. (3) The passing representation-only tests masked the `dream`/`reconciler` replay bug — task-type-specific unique constraints deserve their own coverage.


## Context and Orientation

This section assumes no prior knowledge of the repository. Read it fully before editing.

Honcho runs two cooperating processes over a shared PostgreSQL database and a Redis cache. The **API server** handles HTTP and enqueues background work. The **deriver worker** (started with `uv run python -m src.deriver`) is a long-running loop that consumes that work. This plan touches only the deriver worker, its database schema, its telemetry, and its configuration. Nothing here changes the HTTP API surface.

Key terms, defined once:

- **Queue item**: a row in the `queue` table (SQLAlchemy model `QueueItem` in `src/models.py`, around line 477). Columns today include `id`, `session_id`, `payload` (JSON), `processed` (boolean), `task_type` (text, e.g. `"representation"`), `work_unit_key` (text), `error` (nullable text), `created_at`, `workspace_name`, and `message_id`. There is no attempt counter and no retry timestamp today.

- **Work unit**: a logical grouping of queue items that must be processed together and in order, identified by `work_unit_key`. For representation tasks the key looks like `representation:<workspace>:<session-ish>:<observer>`. Exactly one deriver worker may own a work unit at a time; ownership is coordinated through the `active_queue_sessions` table (SQLAlchemy model `ActiveQueueSession`, around line 536 of `src/models.py`), abbreviated **AQS** below.

- **Representation task**: the deriver work that turns messages into stored conclusions. The batch entry point is `process_representation_batch` in `src/deriver/deriver.py`; the actual LLM call is `honcho_llm_call` (in `src/llm/api.py`) with `enable_retry=True, retry_attempts=3`. Those three retries are IN-CALL retries at the LLM layer; they are not queue-level retries.

- **tenacity / RetryError**: the LLM layer uses the `tenacity` library to retry a call. When all in-call attempts fail, tenacity raises a `RetryError` whose `.last_attempt.exception()` holds the real underlying exception (in our incident, a `ValidationException` raised inside the Codex backend when the provider returned an error/failed/incomplete response). The current code stores `str(RetryError)`, which hides that cause.

- **Poller**: the deriver's loop that repeatedly asks "which work units have pending items and are not currently owned?" and claims some to process. The relevant method is `get_and_claim_work_units` in `src/deriver/queue_manager.py` (around line 330). It selects work units whose items are not yet processed.

- **Backoff**: waiting a growing amount of time between retries (30s, then 60s, then 120s, ...), capped, so a struggling dependency is not hammered.

- **Dead-letter**: the terminal state for a task that has failed its maximum number of attempts. Instead of being silently dropped, it is archived for inspection and manual replay.

The files you will edit or create:

- `src/models.py` — add two columns to `QueueItem`; add a new `QueueDeadLetter` model.
- `migrations/versions/<new>.py` — one new Alembic migration (the project keeps migrations in `migrations/versions/`; recent examples include `f1a2b3c4d5e6_add_reasoning_tree_columns.py`).
- `src/deriver/queue_manager.py` — rewrite failure handling; honor backoff in `get_and_claim_work_units` and `get_queue_item_batch`; add a dead-letter write; add the error-unwrap and classifier helpers or import them.
- `src/deriver/retry_policy.py` — NEW small module holding `unwrap_retry_error`, `is_retryable`, and `compute_backoff`.
- `src/telemetry/events/representation.py` — add a `RepresentationDeadLetteredEvent`.
- `src/config.py` — add retry-tuning settings under the deriver settings block (near `QUEUE_ERROR_RETENTION_SECONDS`, around line 854).
- `config.toml` — document the new knobs under `[deriver]`.
- `tests/deriver/` — new tests mirroring the modules above.

Current failure flow, precisely, so you know what you are replacing. In `src/deriver/queue_manager.py`, `process_work_unit` (around line 611) claims a work unit and loops. For representation tasks it fetches a batch via `get_queue_item_batch`, calls `process_representation_batch`, and on success calls `mark_queue_items_as_processed(items_to_process, work_unit_key)` (marks all items `processed=true`). On failure it calls `_handle_processing_error(e, items_to_process, work_unit_key, ...)` (around line 574), which calls `mark_queue_item_as_errored(items[0], work_unit_key, error_msg)` (around line 1089). That method sets `processed=true, error=<str>` on only the first item. The cleanup sweep in `src/reconciler/queue_cleanup.py` later deletes processed items (immediately if `error IS NULL`, after retention if not).


## Plan of Work

The work proceeds in seven milestones, each independently verifiable. Prefer additive changes; keep tests green between milestones.

Milestone 1 introduces `src/deriver/retry_policy.py` with three pure functions and full unit tests, with no behavior change to the running system yet. `unwrap_retry_error(exc)` recursively peels `tenacity.RetryError` (via `exc.last_attempt.exception()`) to the innermost real cause and returns it; if the argument is not a `RetryError`, it returns the argument unchanged. A companion `format_error(exc)` returns `f"{type(cause).__name__}: {cause}"` for the unwrapped cause, truncated to a safe length. `is_retryable(exc)` returns `False` for a small, explicitly enumerated set of deterministic, configuration-shaped failure messages and `True` otherwise (unknown defaults to retryable). `compute_backoff(attempts, base, cap, jitter)` returns `min(base * 2 ** (attempts - 1), cap)` seconds scaled by a deterministic-in-tests jitter factor in `[1 - jitter, 1 + jitter]`; the jitter source must be injectable so tests are deterministic.

Milestone 2 adds durable state. In `src/models.py`, add to `QueueItem`: `attempts: Mapped[int]` (SmallInteger, not null, server default `0`) and `next_attempt_at: Mapped[datetime | None]` (timezone-aware, nullable). Add a new model `QueueDeadLetter` with columns capturing everything needed to inspect and replay: `id` (own identity primary key), `original_queue_id` (the source `queue.id`), `session_id`, `work_unit_key`, `task_type`, `payload` (JSONB), `workspace_name`, `message_id`, `attempts`, `error` (the unwrapped final cause), `first_failed_at`, `dead_lettered_at` (server default now). Add a supporting partial index on `queue` over `next_attempt_at` where `processed = false` to keep the poller predicate cheap. Write one Alembic migration that adds the two columns (with server defaults so the backfill is free and existing rows become immediately eligible), creates the `queue_dead_letter` table, and creates the index. The migration's `down_revision` must be the current head; find it by running the command in Concrete Steps.

Milestone 3 is the retry engine. Rewrite `_handle_processing_error` so that, for the head item, it: unwraps and formats the error; increments `attempts`; and then either dead-letters (if `not is_retryable(cause)` or `attempts >= MAX_PROCESSING_ATTEMPTS`) or defers (sets `next_attempt_at = now() + compute_backoff(attempts, ...)` and leaves `processed = false`). Dead-lettering must, in one transaction: insert a `QueueDeadLetter` row copying the item's fields, then mark the source `queue` row `processed = true` with the unwrapped `error` set (so the existing retention/cleanup eventually removes the working-queue row while the archive persists). Deferring must set `error` (last cause, for visibility), `attempts`, and `next_attempt_at`, and must NOT set `processed`. Add a new method `defer_queue_item(item, work_unit_key, error, next_attempt_at, attempts)` alongside the existing `mark_queue_item_as_errored`, and repurpose or replace `mark_queue_item_as_errored` as the dead-letter writer (rename to `dead_letter_queue_item` for clarity, keeping the transaction-and-AQS-touch shape it already has). Then make the poller and batch fetch respect backoff: in `get_and_claim_work_units`, both `~models.QueueItem.processed` predicates (around lines 357 and 368) gain an additional condition that the item is eligible now, i.e. `and (QueueItem.next_attempt_at.is_(None) | (QueueItem.next_attempt_at <= func.now()))`; and in `get_queue_item_batch`, the `~models.QueueItem.processed` predicates (around lines 855 and 943) gain the same eligibility condition so a deferred head is skipped while the rest of the batch proceeds, and so a work unit whose only remaining items are all in backoff yields an empty batch (causing the worker to break cleanly and release the AQS). The single-item task path (`get_next_queue_item`, around line 760, predicate near line 783) gets the same eligibility condition so non-representation tasks inherit the retry behavior too.

Milestone 4 makes the loss observable. Add `RepresentationDeadLetteredEvent` to `src/telemetry/events/representation.py` following the existing `RepresentationCompletedEvent` shape (a `BaseEvent` subclass with `_event_type = "representation.dead_lettered"`, `_category = "representation"`, and fields for workspace, session, observed peer, message_id, attempts, and the final error). Emit it at dead-letter time using the same emitter path the deriver already uses for representation events. Also capture the exception to Sentry when `settings.SENTRY.ENABLED`, and add Prometheus counters for retries and dead-letters keyed by workspace and task_type (mirror how `mark_queue_items_as_processed` records `prometheus_metrics.record_deriver_queue_item`).

Milestone 5 exposes tuning. In `src/config.py`, in the deriver settings class near `QUEUE_ERROR_RETENTION_SECONDS`, add `MAX_PROCESSING_ATTEMPTS` (default 10, ge 1), `RETRY_BACKOFF_BASE_SECONDS` (default 30, gt 0), `RETRY_BACKOFF_MAX_SECONDS` (default 7200, gt 0), and `RETRY_BACKOFF_JITTER` (default 0.2, ge 0, le 1). Document them under `[deriver]` in `config.toml` with a one-line comment each. The retry engine reads these from `settings.DERIVER`.

Milestone 6 adds the manual replay operation. Provide a single well-documented function `replay_dead_letters(workspace_name=None, since=None, limit=None)` (place it in `src/deriver/enqueue.py` next to the other enqueue helpers, or a new `src/deriver/dead_letter.py`) that, for matching `queue_dead_letter` rows, inserts fresh `queue` rows (`processed=false`, `attempts=0`, `next_attempt_at=null`, `error=null`, copying `payload`/`work_unit_key`/`task_type`/`workspace_name`/`message_id`/`session_id`) and records that the archive row was replayed (add a nullable `replayed_at` column to `queue_dead_letter` in the Milestone 2 migration to support this). Document the exact invocation in Concrete Steps.

Milestone 7 proves the whole thing end-to-end with the two scenarios in Validation and Acceptance and a green test/typecheck/lint run.


## Concrete Steps

All commands run from the repository root `/home/bex/honcho` unless stated otherwise. The project uses `uv` for Python. Never invoke Python directly; always prefix with `uv run`.

Find the current migration head so your new migration chains correctly:

    uv run alembic heads

Expect a single revision id printed (for example `f1a2b3c4d5e6 (head)`). Use that id as `down_revision` in the new migration.

Create the migration skeleton (you will edit it to add the columns, table, and index described in Milestone 2):

    uv run alembic revision -m "add queue retry columns and dead letter archive"

This writes a new file under `migrations/versions/`. Open it and implement `upgrade()` and `downgrade()`. The `upgrade()` must add the two `queue` columns with server defaults, create the `queue_dead_letter` table, and create the partial index. The `downgrade()` must reverse all three. Server defaults matter: adding `attempts` with `server_default=sa.text("0")` and `next_attempt_at` as nullable means existing rows need no backfill and become eligible immediately.

Apply the migration against your local database (the compose stack must be up; the deriver and database run in Docker):

    uv run alembic upgrade head

Verify the schema:

    docker compose exec -T database psql -U honcho -d honcho -c "\d queue"
    docker compose exec -T database psql -U honcho -d honcho -c "\d queue_dead_letter"

Expect to see the new `attempts` and `next_attempt_at` columns on `queue` and the full `queue_dead_letter` table.

Run the focused tests as you implement each milestone (replace the path with the test you are working on):

    uv run pytest tests/deriver/test_retry_policy.py -q
    uv run pytest tests/deriver/test_queue_retry.py -q

Typecheck and lint before declaring a milestone done:

    uv run basedpyright src/deriver/ src/models.py
    uv run ruff check src/
    uv run ruff format src/

Rebuild and restart the deriver so the running worker picks up code changes (the deriver's source is baked into its image; only `config.toml` and the Codex auth file are mounted, so a code change requires an image rebuild):

    docker compose build deriver
    docker compose up -d deriver

Manual dead-letter replay, once Milestone 6 exists, is invoked through a short `uv run` one-liner (adjust filters as needed):

    uv run python -c "import asyncio; from src.deriver.dead_letter import replay_dead_letters; print(asyncio.run(replay_dead_letters(workspace_name='openclaw')))"

Expect it to print the number of archive rows re-enqueued.


## Validation and Acceptance

Acceptance is behavioral. Two scenarios must be demonstrable, plus a clean test run.

Scenario A — transient failure recovers with zero loss. Write an integration test in `tests/deriver/test_queue_retry.py` that enqueues a representation task and patches the LLM call (`honcho_llm_call`, or the deriver's call site) to raise a transient error wrapped in a `tenacity.RetryError` on its first two invocations and succeed on the third. Drive the queue processing. Assert that: after the first failure the queue item is still present with `processed = false`, `attempts = 1`, and `next_attempt_at` set to a future time; after the retry window (advance the injected clock, or set base backoff to a tiny value in the test settings) the item is processed successfully; the conclusions are written; and NO `queue_dead_letter` row exists. This test must fail against the current code (which drops the item on first exhaustion) and pass after the change.

Scenario B — permanent failure is archived and alerted, not dropped. In the same test module, enqueue a representation task and patch the LLM call to always raise a deterministic, non-retryable error (one whose message is in the `is_retryable` deny-list). Drive processing. Assert that: exactly one attempt is made (non-retryable dead-letters immediately); a `queue_dead_letter` row appears with the unwrapped error, the `payload`, and `attempts = 1`; the source `queue` row is `processed = true`; and a `representation.dead_lettered` telemetry event was emitted (assert via the telemetry emitter test seam the repo already uses for representation events). Additionally assert the max-attempts path: a retryable-but-persistent error dead-letters after exactly `MAX_PROCESSING_ATTEMPTS` attempts.

Full suite, from the repository root:

    uv run pytest tests/ -q

Expect all previously-passing tests to still pass, plus the new tests. Report the counts (for example, "N passed" before and after) in the Outcomes section. Run the TypeScript SDK type-check only if you touched shared schemas (you should not need to for this change):

    cd sdks/typescript && bun run tsc --noEmit

End-to-end smoke against the live stack: after `docker compose build deriver && docker compose up -d deriver`, temporarily point the deriver at a deliberately-broken LLM endpoint (or use a test workspace and a patched build), send a message, and observe in `docker compose logs deriver -f` that the item is deferred with a real error message (not `RetryError[<Future>]`), retried after the backoff, and either succeeds or lands in `queue_dead_letter`. Confirm with:

    docker compose exec -T database psql -U honcho -d honcho -c "SELECT id, attempts, next_attempt_at, left(error,80) FROM queue WHERE processed=false AND attempts>0 ORDER BY next_attempt_at;"
    docker compose exec -T database psql -U honcho -d honcho -c "SELECT count(*), max(dead_lettered_at) FROM queue_dead_letter;"


## Idempotence and Recovery

The Alembic migration is the only destructive-capable step; it is additive (two nullable/defaulted columns, one new table, one index) and fully reversible via `downgrade()`. Re-running `uv run alembic upgrade head` after it is applied is a no-op. If you must roll back, `uv run alembic downgrade -1` removes the columns, table, and index; no existing data in `queue` is modified by the upgrade, so rollback is safe.

The retry engine changes are safe to deploy repeatedly: existing errored rows from before this change (`processed=true, error set, attempts=0`) are already terminal and are untouched by the new logic; the cleanup sweep ages them out per the existing 30-day retention. New failures use the new path. If a deploy is interrupted, the worst case is a mixed fleet where some workers defer-and-retry and others drop; both are safe, and the database columns are compatible with both because the poller predicate treats `next_attempt_at IS NULL` as "eligible now".

Manual replay is idempotent in effect: replaying a `queue_dead_letter` row inserts a fresh `queue` row and stamps `replayed_at`; a filter of "not yet replayed" (`replayed_at IS NULL`) prevents accidental double-enqueue. Because the deriver deduplicates conclusions (`DERIVER.DEDUPLICATE = true`), even an accidental double replay does not create duplicate memory.

Keep the environment clean: the migration leaves the schema in a documented state; no scratch tables are created by the feature itself. (The one-off operational backup table `queue_failed_backup_20260708`, created by hand during the 2026-07-08 incident replay, is unrelated to this feature and may be dropped once its rows are confirmed reprocessed.)


## Artifacts and Notes

Evidence from the incident that motivates this plan (keep concise):

    -- All dropped items were representation tasks in one workspace, RetryError-wrapped:
    task_type=representation, workspace=openclaw, error head="RetryError: RetryError[<Future ... raised ValidationException>]"
    daily counts: 2026-07-03 = 484, 2026-07-07 = 3, 2026-07-08 = 66

    -- The endpoint was healthy when tested; failures were time-clustered (transient), not message-specific:
    a single deriver-style Codex call and a 24-way concurrent burst both returned 24/24 OK during investigation.

Expected shape of the new `_handle_processing_error` head-item logic (prose, to be turned into code):

    cause = unwrap_retry_error(error)
    message = format_error(cause)
    attempts = item.attempts + 1
    if not is_retryable(cause) or attempts >= settings.DERIVER.MAX_PROCESSING_ATTEMPTS:
        dead_letter_queue_item(item, work_unit_key, message, attempts)   # archive + mark processed
        emit RepresentationDeadLetteredEvent(...); sentry capture; increment dead_letter counter
    else:
        delay = compute_backoff(attempts, base, cap, jitter)
        defer_queue_item(item, work_unit_key, message, now()+delay, attempts)  # keep unprocessed
        increment retry counter


## Interfaces and Dependencies

Use the existing libraries already vendored in the project: `sqlalchemy` (async), `alembic`, `pydantic`/`pydantic-settings`, `tenacity`, and the project's own `src.telemetry` and `src.config`. Do not add new dependencies.

In `src/deriver/retry_policy.py`, define:

    def unwrap_retry_error(exc: BaseException) -> BaseException: ...
    def format_error(exc: BaseException, *, limit: int = 2000) -> str: ...
    def is_retryable(exc: BaseException) -> bool: ...
    def compute_backoff(attempts: int, base_seconds: float, cap_seconds: float, jitter: float, *, rng: Callable[[], float] | None = None) -> float: ...

In `src/models.py`, `QueueItem` gains:

    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

and a new model is added:

    class QueueDeadLetter(Base):
        __tablename__ = "queue_dead_letter"
        id: Mapped[str]                 # nanoid primary key, matching the repo's text-ID convention
        original_queue_id: Mapped[int | None]
        session_id: Mapped[str | None]
        work_unit_key: Mapped[str]
        task_type: Mapped[str]
        payload: Mapped[dict[str, Any]]
        workspace_name: Mapped[str | None]
        message_id: Mapped[int | None]
        attempts: Mapped[int]
        error: Mapped[str | None]
        first_failed_at: Mapped[datetime.datetime | None]
        dead_lettered_at: Mapped[datetime.datetime]     # server default now()
        replayed_at: Mapped[datetime.datetime | None]

In `src/deriver/queue_manager.py`, the `QueueManager` class gains/loses:

    async def defer_queue_item(self, item: QueueItem, work_unit_key: str, error: str, next_attempt_at: datetime.datetime, attempts: int) -> None: ...
    async def dead_letter_queue_item(self, item: QueueItem, work_unit_key: str, error: str, attempts: int) -> None: ...   # replaces mark_queue_item_as_errored
    # _handle_processing_error rewritten to route to one of the above via retry_policy

In `src/telemetry/events/representation.py`, add:

    class RepresentationDeadLetteredEvent(BaseEvent):
        _event_type: ClassVar[str] = "representation.dead_lettered"
        _category: ClassVar[str] = "representation"
        workspace_name: str
        session_name: str | None
        observed: str | None
        message_id: str | None
        attempts: int
        error: str

In `src/config.py`, the deriver settings class gains:

    MAX_PROCESSING_ATTEMPTS: Annotated[int, Field(default=10, ge=1)] = 10
    RETRY_BACKOFF_BASE_SECONDS: Annotated[float, Field(default=30.0, gt=0)] = 30.0
    RETRY_BACKOFF_MAX_SECONDS: Annotated[float, Field(default=7200.0, gt=0)] = 7200.0
    RETRY_BACKOFF_JITTER: Annotated[float, Field(default=0.2, ge=0.0, le=1.0)] = 0.2

In `src/deriver/dead_letter.py` (new), define:

    async def replay_dead_letters(workspace_name: str | None = None, since: datetime.datetime | None = None, limit: int | None = None) -> int: ...

These signatures are the contract for the milestones; keep names and paths stable so tests and callers can rely on them.


## Change Log for this Plan

- 2026-07-08, Sky: Initial authoring. Root cause established (transient Codex Responses API failures wrapped as `RetryError[ValidationException]`, silently dropped by the head-item-drop failure path). Chose poller-driven backoff over a new scheduler, per-item deferral over batch deferral, MAX_PROCESSING_ATTEMPTS=10 with a 2-hour backoff cap (~4-hour survival window) to ride out multi-hour outages, and a permanent `queue_dead_letter` archive independent of queue retention. All decisions recorded in the Decision Log with rationale.
