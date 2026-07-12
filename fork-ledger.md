# Fork Ledger

This file is Honcho's fork carry contract. The replay unit is behavior, not old commits. Reimplement each seam against current upstream and re-prove the behavior through the runtime, config, or service surface that actually uses it.

Current replay target: `upstream-main@602347d76c4e5e464e883f5df32b5bad5aa7952b`

The fork base is post-`v3.0.11`; using the exact merge-base avoids treating upstream's own post-tag commits as fork behavior. Upstream has since advanced to `73453f892d8a44e322447dfe06db969caeb200a4`, which is the next candidate upgrade target.

Replay classification:

- **Runtime carry:** product or deployment behavior still requiring fork code.
- **Partial-overlap carry:** upstream covers part of the contract, but not enough to drop it.
- **Support/proof carry:** local operations, tests, skills, or migration tooling; excluded from runtime conflict triage.

## upstream-main@602347d seam necessity review

Replay summary: fork head `17b8676dd94e` contains 13 fork-only commits, including two duplicate-history pairs. They reduce to 11 reviewed behavioral seams: 7 runtime carries and 4 support/proof carries. No seam is currently absorbed by the seeded target.

| Seam | Decision | Importance | upstream-main@602347d evidence / replay note |
| --- | --- | --- | --- |
| codex-subscription-transport | Runtime carry | Critical | Target has no Codex transport, auth-file refresh, registry entry, or Responses API backend; fork implements them in `src/llm/` and config. |
| gpt56-responses-lite | Runtime carry | Critical | Fork adds the Responses-Lite header and GPT-5.6 input contract at `src/llm/backends/codex.py:27` and `:405`; target has neither. Review first because upstream or the subscription protocol may absorb/change it. |
| deriver-retry-durability | Runtime carry | Critical | Fork defers retryable failures with bounded backoff and archives exhausted/non-retryable rows (`src/deriver/queue_manager.py:600`, `src/models.py:549`); target marks failures without this durable state machine. |
| production-model-policy | Runtime carry | High | `config.toml` and `AGENTS.md` encode the live self-hosted model, tier, effort, concurrency, embedding, and fallback policy consumed by the Docker stack. This is deployment policy, not an upstream default candidate. |
| chatgpt-oauth-mcp | Runtime carry | Critical | Fork adds stateful Streamable HTTP MCP, OAuth discovery/DCR/PKCE/token persistence, proxy-aware public URLs, fixed ChatGPT identities, and MCP tests; target only has the Worker implementation. |
| local-codex-stdio-mcp | Runtime carry | High | Fork adds the local stdio entrypoint with fixed `openclaw`/`codex` identity (`mcp/src/stdio.ts:7`) and wrapper used by standalone Codex. |
| local-compose-port-isolation | Runtime carry | Medium | `docker-compose.override.yml` deliberately keeps Honcho Postgres off the host's port 5432 so Hindsight can own it. |
| mcp-identity-data-hygiene | Support/proof carry | Medium | Repeatable SQL repairs merge historical ChatGPT aliases and canonicalize Rebecca Clair / Bex peer-card and conclusion data. Run only against explicitly selected data. |
| private-memory-importer | Support/proof carry | Medium | Local importer replays OpenClaw private-memory exports into Honcho with the expected workspace and identity conventions. |
| dream-health-watcher | Support/proof carry | High | Sanitized watcher turns queue state and deriver logs into actionable exit codes without leaking raw memory content. |
| bundled-agent-skills | Support/proof carry | Medium | Fork bundles Honcho inspection/integration and Python/TypeScript migration skills for local agents; duplicate `migrate skills` commits represent one seam. |

## Replay impact

- Preserve Codex subscription transport and GPT-5.6 Responses-Lite as separate seams: the base transport may remain necessary after the model-specific protocol is absorbed.
- Preserve deriver retry columns, migration, dead-letter archive, replay path, telemetry, and poison-item isolation as one atomic durability contract.
- Preserve ChatGPT HTTP MCP and local Codex stdio MCP as separate identity/security boundaries even though they share tool registration.
- Treat tracked production model settings and Compose isolation as local deployment policy; never replace them wholesale with new upstream defaults.
- Keep data-repair SQL and memory importer opt-in. Their presence is carried; executing them is not part of an upgrade replay.
- Revalidate bundled skills and operational documentation after paths, SDK APIs, or commands change upstream.

## Seam inventory

### codex-subscription-transport

Carry behavior: Honcho can use a Codex/ChatGPT subscription as an LLM transport without an OpenAI API key. Credentials are loaded and refreshed from the Codex auth file; requests support streaming, structured output, tools, retry-on-auth-failure, concurrency limiting, telemetry, and normal model registry/config plumbing.

Primary seam files:

- `src/llm/backends/codex.py`
- `src/llm/codex_auth.py`
- `src/llm/credentials.py`
- `src/llm/registry.py`
- `src/llm/types.py`
- `src/config.py`
- `tests/llm/test_backends/test_codex.py`
- `tests/llm/test_codex_auth.py`
- `tests/llm/test_codex_registry.py`
- `tests/llm/test_codex_tool_loop.py`

Primary seam tests:

- `.venv/bin/pytest -q tests/llm/test_backends/test_codex.py tests/llm/test_codex_auth.py tests/llm/test_codex_registry.py tests/llm/test_codex_tool_loop.py tests/llm/test_credentials.py tests/llm/test_model_config.py tests/llm/test_telemetry_llm_call.py`
- Deferred live proof: run `tests/live_llm/test_live_codex.py` only with the mounted Codex subscription auth file; never substitute an OpenAI API key without explicit approval.

Rebase notes:

- Source commits: `bd8e1b32f4`, duplicate-history equivalent `e4fcb32fd7`, and roadmap follow-up `afb8e9aa64`.
- Keep transport registration and credential selection provider-neutral. Do not leak subscription tokens into config, logs, tests, or commits.
- Drop only when upstream provides an equivalent subscription-authenticated Codex transport and focused plus live tests prove parity.

### gpt56-responses-lite

Carry behavior: GPT-5.6 Codex models use the internal Responses-Lite request contract: protocol header, developer instruction/input items, additional tools, encrypted reasoning context, and supported Codex client identity.

Primary seam files:

- `src/llm/backends/codex.py`
- `src/config.py`
- `config.toml`
- `tests/llm/test_backends/test_codex.py`
- `tests/llm/test_codex_tool_loop.py`

Primary seam tests:

- `.venv/bin/pytest -q tests/llm/test_backends/test_codex.py tests/llm/test_codex_tool_loop.py`
- Deferred live proof: one structured-output call and one tool-loop call with the configured GPT-5.6 model.

Rebase notes:

- Source commit: `870ed1e498`.
- Re-check the current subscription protocol and model identity on every upgrade; this seam is intentionally easy to absorb or revise independently of the base Codex transport.
- Drop when the active endpoint accepts upstream's request shape and all local structured/tool-loop proof passes without the fork path.

### deriver-retry-durability

Carry behavior: transient deriver failures are retried with bounded exponential backoff; terminal or exhausted poison items are independently archived and replayable, while later queue items remain pending instead of being discarded.

Primary seam files:

- `src/deriver/retry_policy.py`
- `src/deriver/dead_letter.py`
- `src/deriver/queue_manager.py`
- `src/models.py`
- `src/config.py`
- `src/telemetry/events/representation.py`
- `migrations/versions/33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py`
- `tests/deriver/test_retry_policy.py`
- `tests/deriver/test_queue_retry.py`

Primary seam tests:

- `.venv/bin/pytest -q tests/deriver/test_retry_policy.py tests/deriver/test_queue_retry.py tests/alembic/revisions/test_33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py`
- Deferred live proof: inspect queue attempts/`next_attempt_at`, archive a controlled terminal failure, replay it, and verify subsequent work still processes.

Rebase notes:

- Source commits: `cb00d9f2e0`; design record `d179a9f`; deployment tuning in `6b2bdfdcf9`.
- Migration, ORM model, claim query, state transitions, replay helper, and telemetry are one invariant frontier. Never replay only the Python logic.
- Drop only when upstream provides equivalent durable scheduling, poison isolation, independent dead-letter retention, and replay semantics.

### production-model-policy

Carry behavior: the live self-hosted stack uses locally selected model transports, models, effort levels, service tiers, concurrency, retries, embeddings, and fallback behavior, with operational lessons documented beside the governing config.

Primary seam files:

- `config.toml`
- `AGENTS.md`
- `ROADMAP.md`
- `.gitignore`

Primary seam tests:

- `python -c 'import tomllib; tomllib.load(open("config.toml", "rb")); print("toml-ok")'`
- `docker compose config --quiet`
- Deferred live proof: restart only when deploying an upgrade, then read settings from `honcho-api-1` and `honcho-deriver-1` and run `openclaw honcho status`.

Rebase notes:

- Source commits: `f0639a0954`, `6b2bdfdcf9`, and `870ed1e498`.
- Merge new upstream settings field-by-field. Preserve secrets outside Git and preserve the explicit no-OpenAI-key rule.
- This seam is local deployment policy and is not expected to become upstream-eligible.

### chatgpt-oauth-mcp

Carry behavior: `honcho.heliasar.com` exposes a persistent, proxy-aware Streamable HTTP MCP server authenticated by owner-password OAuth with DCR, PKCE, exact redirect validation, refresh/revocation, persisted hashed tokens, and server-side Honcho credentials/identity.

Primary seam files:

- `mcp/src/http.ts`
- `mcp/src/node-config.ts`
- `mcp/src/oauth-provider.ts`
- `mcp/src/oauth-store.ts`
- `mcp/src/server.ts`
- `mcp/src/tools.ts`
- `mcp/src/peer-identity.ts`
- `mcp/src/http.test.ts`
- `mcp/src/node-config.test.ts`
- `mcp/src/oauth-store.test.ts`
- `mcp/src/peer-identity.test.ts`

Primary seam tests:

- `cd mcp && bun run typecheck:node && bun test`
- Deferred live proof: local health/challenge/discovery and authenticated MCP lifecycle, then public Cloudflare OAuth and a real ChatGPT tool call with token survival across service restart.

Rebase notes:

- Source commit: `7d5922df61`.
- Keep trust at exactly one proxy hop, public HTTPS metadata, redirect allowlisting, token hashing, fixed lowercase identity mapping (`sarah` assistant, `bex` human), and server-side Honcho authorization.
- Drop only if upstream ships an equivalent Node HTTP/OAuth deployment whose security and identity contracts pass these tests.

### local-codex-stdio-mcp

Carry behavior: standalone Codex connects through a local stdio MCP wrapper using workspace `openclaw` and peer identity `codex`, without sharing ChatGPT's OAuth or `sarah` identity boundary.

Primary seam files:

- `mcp/honcho-mcp-stdio`
- `mcp/src/stdio.ts`
- `mcp/src/server.ts`
- `mcp/src/config.ts`
- `mcp/instructions.md`

Primary seam tests:

- `cd mcp && bun run typecheck:node`
- Run the stdio smoke in `AGENTS.md` and verify instructions plus tool listing.

Rebase notes:

- Source commit: `7d5922df61`.
- Preserve separate defaults and instructions for `codex`; do not route local Codex through ChatGPT's OAuth identity.
- This can be dropped if the local client adopts another transport with the same identity isolation and live tool proof.

### local-compose-port-isolation

Carry behavior: Honcho's database remains private to the Compose network and does not claim host port 5432.

Primary seam files:

- `docker-compose.override.yml`

Primary seam tests:

- `docker compose config --format json | python -c 'import json,sys; p=json.load(sys.stdin)["services"]["database"].get("ports", []); assert p == [], p; print("database-ports-ok")'`

Rebase notes:

- Source commit: `870ed1e498`.
- Re-evaluate if upstream renames the database service or changes Compose merge semantics.
- Drop only if the base Compose file no longer publishes the database port.

### mcp-identity-data-hygiene

Carry behavior: reviewed SQL migrations repair historical alias collisions and converge peer metadata and memory text on canonical Rebecca Clair / preferred-name Bex conventions.

Primary seam files:

- `mcp/scripts/merge-chatgpt-peer-aliases.sql`
- `mcp/scripts/replace-rebecca-clair-display-name.sql`
- `mcp/scripts/replace-rebecca-clair-in-memory.sql`
- `mcp/scripts/canonicalize-sarah-bex-peer-card.sql`

Primary seam tests:

- Review each script inside a transaction against a database snapshot and inspect affected-row counts before commit.

Rebase notes:

- Source commit: `7d5922df61`.
- These scripts are repeatable operational records, not automatic startup migrations. Never execute them merely because an upgrade replays the files.
- Retire individual scripts after the relevant data invariant is permanently enforced and no historical environment needs repair.

### private-memory-importer

Carry behavior: OpenClaw private-memory exports can be normalized and replayed into the local Honcho workspace without manual per-record API work.

Primary seam files:

- `scripts/import_openclaw_private_memory.py`

Primary seam tests:

- `python -m py_compile scripts/import_openclaw_private_memory.py`
- Use dry-run/help or a disposable workspace for behavioral proof; never replay production memory during an upgrade adoption pass.

Rebase notes:

- Source commit: `2e929cf7e5`.
- Revalidate API/SDK payload shapes and identity mapping against the selected target.
- Keep as local support unless upstream adopts the same export format and import contract.

### dream-health-watcher

Carry behavior: operators can detect stalled/failed dream and replay work through sanitized queue/log summaries and stable exit codes without exposing raw private memory.

Primary seam files:

- `scripts/watch_dream_health.py`
- `AGENTS.md`

Primary seam tests:

- `python -m py_compile scripts/watch_dream_health.py`
- Deferred live proof: `scripts/watch_dream_health.py --since 15m`; preserve exit codes 0/1/2 and sanitized output.

Rebase notes:

- Source commit: `6421a9d41a`.
- Update log matchers when upstream changes worker messages or queue schema; privacy redaction is invariant.
- Keep as local support unless upstream provides equivalent sanitized operational health output.

### bundled-agent-skills

Carry behavior: local agents have repo-versioned instructions for inspecting Honcho, integrating it into Python/TypeScript applications, and migrating both SDKs.

Primary seam files:

- `.agents/skills/honcho-cli/`
- `.agents/skills/honcho-integration/`
- `.agents/skills/migrate-honcho-py/`
- `.agents/skills/migrate-honcho-ts/`

Primary seam tests:

- Verify every skill has a readable `SKILL.md`, all referenced relative files exist, and commands/API examples match the replayed SDK versions.

Rebase notes:

- Source commits: duplicate-history pair `f326954f96` and `10d1e47761`.
- Refresh examples and migration deltas when upstream changes SDK versions or repository paths.
- These are support assets; do not count their conflicts as runtime-product conflicts.

## Narrow validation set

- `python /home/bex/.agents/skills/fork-repo/scripts/validate_fork_ledger.py --repo /home/bex/honcho`
- `.venv/bin/pytest -q tests/llm/test_backends/test_codex.py tests/llm/test_codex_auth.py tests/llm/test_codex_registry.py tests/llm/test_codex_tool_loop.py tests/llm/test_credentials.py tests/llm/test_model_config.py tests/llm/test_telemetry_llm_call.py`
- `.venv/bin/pytest -q tests/deriver/test_retry_policy.py tests/deriver/test_queue_retry.py tests/alembic/revisions/test_33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py`
- `(cd mcp && bun run typecheck:node && bun test)`
- `python -m py_compile scripts/import_openclaw_private_memory.py scripts/watch_dream_health.py`
- `python -c 'import tomllib; tomllib.load(open("config.toml", "rb")); print("toml-ok")'`
- `docker compose config --quiet`
- `git diff --check`

## Deferred live proof

- Codex subscription calls require the mounted auth file and active subscription endpoint.
- Deriver retry/dead-letter proof requires a controlled live queue item and database inspection.
- Public MCP proof requires Cloudflare, OAuth, the user service, and a real ChatGPT connector call.
- Stdio proof requires the installed local Codex MCP configuration.
- Memory repair/import scripts require an explicitly selected database or disposable workspace and affected-row review.
- Production config proof requires intentional container restart followed by running-settings and OpenClaw status read-back.

## Upgrade procedure

1. Fetch `upstream`, select an exact tag or commit, and create a non-`main` replay branch from it.
2. Copy this ledger and the fork contract in `AGENTS.md` onto the replay branch before runtime code.
3. Re-evaluate every seam against the target and classify it as runtime carry, partial-overlap carry, support/proof carry, or absorbed.
4. Reimplement required behavior in dependency order: model/config foundations, Codex transport, deriver schema/state machine, MCP shared core and transports, deployment policy, then support tooling.
5. Run each seam's focused proof. Perform deferred live proof only on the authorized deployment surface.
6. Update evidence, absorption decisions, source/target references, and replay summary before merging.

Never replay the fork by rebasing or cherry-picking its historical commit series wholesale. Never perform an upgrade replay directly on `main`.
