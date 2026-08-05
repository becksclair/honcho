# Fork Ledger

This file is Honcho's fork carry contract for humans, coding agents, and future replay tooling. The replay unit is behavior, not old commits. Reimplement each seam against current upstream and re-prove the behavior through the runtime, config, or service surface that actually uses it.

Current replay target: `upstream-main@602347d76c4e5e464e883f5df32b5bad5aa7952b`

The fork base is post-`v3.0.11`; using the exact merge-base avoids treating upstream's own post-tag commits as fork behavior. Upstream has since advanced to `73453f892d8a44e322447dfe06db969caeb200a4`, which is the next candidate upgrade target.

Replay classification:

- **Runtime carry:** product or deployment behavior still requiring fork code.
- **Partial-overlap carry:** upstream covers part of the contract, but not enough to drop it.
- **Support/proof carry:** local operations, tests, skills, or migration tooling; excluded from runtime conflict triage.

## upstream-main@602347d seam necessity review

Replay summary: runtime fork head `17b8676dd94e7434e103263598924f290913c957` was adopted by ledger commit `2b562212a103859a34bf3bc8e73a9dd9152b92e3`. Its 13 fork-only runtime/support commits, including two duplicate-history pairs, reduce to 13 reviewed behavioral seams: 9 runtime carries and 4 support/proof carries. No seam is currently absorbed by the seeded target.

| Seam | Decision | Importance | upstream-main@602347d evidence / replay note |
| --- | --- | --- | --- |
| codex-subscription-transport | Runtime carry | Critical | Target has no Codex transport, auth-file refresh, registry entry, or Responses API backend; fork implements them in `src/llm/` and config. |
| gpt56-responses-lite | Runtime carry | Critical | Fork adds the Responses-Lite header and GPT-5.6 input contract at `src/llm/backends/codex.py:27` and `:405`; target has neither. Review first because upstream or the subscription protocol may absorb/change it. |
| deriver-retry-durability | Runtime carry | Critical | Fork defers retryable failures with bounded backoff and archives exhausted/non-retryable rows (`src/deriver/queue_manager.py:600`, `src/models.py:549`); target marks failures without this durable state machine. |
| production-model-policy | Runtime carry | High | `config.toml` and `AGENTS.md` encode the live self-hosted model, tier, effort, concurrency, embedding, and fallback policy consumed by the Docker stack. This is deployment policy, not an upstream default candidate. |
| chatgpt-oauth-mcp | Runtime carry | Critical | Fork adds stateful Streamable HTTP MCP, OAuth discovery/DCR/PKCE/token persistence, proxy-aware public URLs, and MCP tests; target only has the Worker implementation. |
| mcp-peer-identity-normalization | Runtime carry | Critical | `mcp/src/peer-identity.ts:1-12` canonicalizes ChatGPT aliases and shared peer/session tools apply it before writes; target accepts caller-provided peer IDs verbatim. |
| workspace-collective-recall-and-identity | Runtime carry | Critical | Fork adds workspace-configured canonical peer aliases, strict server-side identity enforcement, and Dialectic retrieval across a configured observer set before deduplication/synthesis; target isolates recall to one observer and permits case/runtime drift. |
| fam-workspace-migration | Support/proof carry | Critical | Manifest-driven migration tooling converges `openclaw` and `hermes` into `fam` while preserving message/document IDs, embeddings, reasoning provenance, authoritative peer cards, queue history, archive sessions, and rollback gates. |
| mcp-conclusion-query-controls | Runtime carry | High | `mcp/src/tools/conclusions.ts:24-58` and `:87-120` add level, pagination, distance, ordering, and returned-level support; target exposes only unfiltered list/query calls. |
| local-codex-stdio-mcp | Runtime carry | High | Fork adds the local stdio entrypoint with fixed `openclaw`/`codex` identity (`mcp/src/stdio.ts:7`) and wrapper used by standalone Codex. |
| local-compose-port-isolation | Runtime carry | Medium | `docker-compose.override.yml` deliberately keeps Honcho Postgres off the host's port 5432 so Hindsight can own it. |
| mcp-identity-data-hygiene | Support/proof carry | Medium | Repeatable SQL repairs merge historical ChatGPT aliases and canonicalize Rebecca Clair / Bex peer-card and conclusion data. Run only against explicitly selected data. |
| private-memory-importer | Support/proof carry | Medium | Local importer replays OpenClaw private-memory exports into Honcho with the expected workspace and identity conventions. |
| dream-health-watcher | Support/proof carry | High | Watcher turns queue state and matched deriver log events into actionable exit codes with bounded, best-effort token/identifier redaction; its snippets remain sensitive operator output. |
| bundled-agent-skills | Support/proof carry | Medium | Fork bundles Honcho inspection/integration and Python/TypeScript migration skills for local agents; duplicate `migrate skills` commits represent one seam. |

## Replay impact

- `codex-subscription-transport` - runtime carry: retain subscription-authenticated Codex transport, credential refresh, tool/structured-output support, and registry plumbing without an OpenAI API key.
- `gpt56-responses-lite` - runtime carry: retain the model-specific Responses-Lite request shape and client identity independently of the base Codex transport.
- `deriver-retry-durability` - runtime carry: retain retry columns, bounded backoff, dead-letter archive/replay, telemetry, and poison-item isolation as one atomic state-machine contract.
- `production-model-policy` - runtime carry: retain the tracked model/tier/effort/concurrency/embedding policy consumed by the live self-hosted stack.
- `chatgpt-oauth-mcp` - runtime carry: retain proxy-aware HTTP MCP plus owner-password OAuth, persisted clients/tokens, and server-side Honcho authorization.
- `mcp-peer-identity-normalization` - runtime carry: canonicalize ChatGPT aliases before peer creation, session membership, removal, or message ingestion.
- `workspace-collective-recall-and-identity` - runtime carry: keep canonical identities server-authoritative and federate Dialectic evidence across the configured workspace observer set before ranking, deduplication, provenance formatting, and synthesis.
- `fam-workspace-migration` - support/proof carry: retain the guarded, manifest-driven convergence and verification tooling; never run it without stopped writers and a verified database backup.
- `mcp-conclusion-query-controls` - runtime carry: expose conclusion level, pagination, distance, ordering, and returned level through MCP.
- `local-codex-stdio-mcp` - runtime carry: retain local stdio access with the separate `codex` identity boundary.
- `local-compose-port-isolation` - runtime carry: keep Honcho Postgres private to Compose so another local service can own host port 5432.
- `mcp-identity-data-hygiene` - support/proof carry: retain opt-in historical alias/peer-card/conclusion repair SQL without executing it during replay.
- `private-memory-importer` - support/proof carry: retain the OpenClaw private-memory normalization/import utility without replaying production data during upgrades.
- `dream-health-watcher` - support/proof carry: retain concise queue/log health summaries, best-effort secret/identifier redaction, and stable operator exit codes while treating snippets as sensitive.
- `bundled-agent-skills` - support/proof carry: retain repo-versioned Honcho inspection, integration, and SDK migration instructions.

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
- `.gitignore`
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
- `docs/plans/deriver-retry-durability.md`
- `tests/deriver/test_retry_policy.py`
- `tests/deriver/test_queue_retry.py`

Primary seam tests:

- `python -m py_compile src/deriver/retry_policy.py src/deriver/dead_letter.py src/deriver/queue_manager.py migrations/versions/33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py`
- Isolated database proof: `.venv/bin/pytest -n 1 -q tests/deriver/test_retry_policy.py tests/deriver/test_queue_retry.py tests/alembic/revisions/test_33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py` with `DB_CONNECTION_URI` pointed at a disposable Postgres instance. Do not point this suite at the live Honcho database.
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

Primary seam tests:

- `python -c 'import tomllib; tomllib.load(open("config.toml", "rb")); print("toml-ok")'`
- `docker compose -f docker-compose.yml.example -f docker-compose.override.yml config --quiet`
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
- `mcp/src/config.ts`
- `mcp/instructions.md`
- `mcp/package.json`
- `mcp/bun.lock`
- `mcp/tsconfig.json`
- `mcp/tsconfig.node.json`
- `mcp/README.md`
- `mcp/src/http.test.ts`
- `mcp/src/node-config.test.ts`
- `mcp/src/oauth-store.test.ts`
- `mcp/src/peer-identity.test.ts`

Primary seam tests:

- `cd mcp && bun run typecheck:node && bun test`
- Deferred live proof: local health/challenge/discovery and authenticated MCP lifecycle, then public Cloudflare OAuth and a real ChatGPT tool call with token survival across service restart.

Rebase notes:

- Source commit: `7d5922df61`.
- External consumers: `/home/bex/personal/dotfiles/configs/dot_config/systemd/user/honcho-mcp.service`, `/home/bex/personal/dotfiles/configs/dot_config/systemd/user/watercooler-cf-tunnel.service`, and `/home/bex/.cloudflared/config.yml` bind the built Node artifact to port 7678 and the public hostname.
- Keep trust at exactly one proxy hop, public HTTPS metadata, hostname allowlisting during dynamic registration, exact equality with the registered redirect URI during code exchange, token hashing, and server-side Honcho authorization.
- Drop only if upstream ships an equivalent Node HTTP/OAuth deployment whose security and identity contracts pass these tests.

### mcp-peer-identity-normalization

Carry behavior: peer IDs emitted by ChatGPT are canonicalized at every shared MCP write boundary: `Sarah`/`sarah` become `sarah`, while `Rebecca_Clair`/`Bex`/`bex` become `bex`. Node configuration independently fixes the deployment's human and assistant identities so callers cannot override server authorization or identity defaults.

Primary seam files:

- `mcp/src/peer-identity.ts`
- `mcp/src/tools/peers.ts`
- `mcp/src/tools/sessions.ts`
- `mcp/src/node-config.ts`
- `mcp/src/peer-identity.test.ts`
- `mcp/src/node-config.test.ts`

Primary seam tests:

- `cd mcp && bun test`
- Deferred integration proof: invoke peer creation, session peer add/remove, and message ingestion using every historical alias and inspect the canonical Honcho IDs.

Rebase notes:

- Source commit: `7d5922df61`.
- Keep normalization in the shared tool layer rather than only the HTTP transport; otherwise aliases can bypass it through another runtime.
- Drop only when upstream provides configurable canonical peer aliases plus fixed server-side identity with equivalent write-boundary coverage.

### mcp-conclusion-query-controls

Carry behavior: MCP clients can inspect explicit, deductive, or inductive conclusions directly, paginate/reverse list results, constrain semantic-query distance, and receive each conclusion's level in results.

Primary seam files:

- `mcp/src/tools/conclusions.ts`

Primary seam tests:

- `cd mcp && bun run typecheck:node`
- Deferred integration proof: list and query each conclusion level against a disposable or read-only peer and verify pagination, distance, ordering, and returned `level` fields.

Rebase notes:

- Source commit: `7d5922df61`.
- Preserve the Honcho SDK argument order and level-filter shape when upgrading the SDK; this seam currently lacks a focused mocked tool test, so the integration proof is mandatory during replay.
- Drop when upstream's MCP tools expose equivalent filter and result contracts.

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

- `docker compose -f docker-compose.yml.example -f docker-compose.override.yml config --format json | python -c 'import json,sys; p=json.load(sys.stdin)["services"]["database"].get("ports", []); assert p == [], p; print("database-ports-ok")'`

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

- Restore a disposable database snapshot, capture explicit preflight counts, execute the script, inspect post-state and affected invariants, then discard the database. The scripts own and commit their transactions; they cannot be wrapped for review-before-commit.

Rebase notes:

- Source commit: `7d5922df61`.
- These scripts are repeatable operational records, not automatic startup migrations. Never execute them merely because an upgrade replays the files.
- Treat every invocation as destructive and self-committing. Require a verified backup/snapshot and a disposable rehearsal before any authorized production use.
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

Carry behavior: operators can detect stalled/failed dream and replay work through concise queue/log summaries and stable exit codes. The watcher redacts a bounded set of tax IDs, email addresses, UUIDs, and token-like strings, but matched log snippets can still contain private memory and must be handled as sensitive operator output.

Primary seam files:

- `scripts/watch_dream_health.py`
- `AGENTS.md`

Primary seam tests:

- `python -m py_compile scripts/watch_dream_health.py`
- Deferred live proof: `scripts/watch_dream_health.py --since 15m`; preserve exit codes 0/1/2, verify known redactors, and inspect output only in a private operator surface.

Rebase notes:

- Source commit: `6421a9d41a`.
- Update log matchers when upstream changes worker messages or queue schema. Best-effort secret/identifier redaction is invariant, but the output is not a privacy boundary.
- Keep as local support unless upstream provides equivalent concise operational health output.

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

### workspace-collective-recall-and-identity

Carry behavior: a workspace can declare canonical peer aliases and a strict identity policy, and the Dialectic can retrieve one globally ranked evidence pool across a configured observer set. Observer-specific conclusions remain distinct at rest and retain provenance, while normal `peer.chat()` calls transparently use the shared family memory scope. Raw message fallback uses the union of sessions visible to the recall observers, and context adjacency remains session-safe.

Primary seam files:

- `src/schemas/configuration.py`
- `src/identity.py`
- `src/crud/peer.py`
- `src/crud/session.py`
- `src/crud/message.py`
- `src/crud/document.py`
- `src/dialectic/chat.py`
- `src/dialectic/core.py`
- `src/dialectic/retrieval.py`
- `src/dialectic/prompts.py`
- `src/utils/agent_tools.py`
- `src/security.py`
- `src/routers/`
- `mcp/src/peer-identity.ts`

Primary seam tests:

- `.venv/bin/pytest -q tests/test_identity.py tests/dialectic/test_collective_recall.py tests/test_security.py tests/routes/test_auth_route_policy.py tests/routes/test_conclusions.py tests/routes/test_keys.py tests/routes/test_messages.py tests/routes/test_peers.py tests/routes/test_sessions.py tests/routes/test_workspaces.py`
- `(cd mcp && bun run typecheck:node && bun test)`

Rebase notes:

- Preserve backward compatibility: workspaces without an identity block remain preserve-case and allow dynamic peers; the strict policy is opt-in.
- Keep writes observer-local. Widen only read-time candidate retrieval and message visibility.
- Globally rank pgvector results in one SQL query; external stores must retain and merge scores across namespaces.
- Keep observer provenance in the synthesized evidence and reasoning-chain traversal.

### fam-workspace-migration

Carry behavior: a reviewed JSON manifest defines canonical family peers, alias mappings, authoritative peer cards, source workspaces, excluded archive sessions, client identities, and collective recall configuration. The migration operation preflights queue/session/peer invariants, requires stopped writers and a verified backup, moves the selected corpus transactionally while preserving message/document IDs and embeddings, rewrites queue/dead-letter provenance, retires source workspaces, and verifies the final `fam` state before commit.

Primary seam files:

- `scripts/migrate_fam_workspace.py`
- `src/operations/fam_migration.py`
- `tests/operations/test_fam_migration.py`
- deployment manifest and client configurator in the Saga homelab repository

Primary seam tests:

- `.venv/bin/pytest -q tests/operations/test_fam_migration.py`
- Disposable-Postgres preflight and apply rehearsal using the exact production manifest.
- Deferred live proof: verified `pg_dump`, stopped writers, migration apply/verify, client cutover, then Hermes cross-observer recall checks.

Rebase notes:

- This is operational tooling, not an Alembic schema migration. Never execute during ordinary startup or upgrade replay.
- Preserve source workspace archive sessions and full-database rollback; do not create a second live duplicate corpus.
- Revalidate every source peer/session exception against current production data before use.

## Upgrade procedure

1. Fetch `upstream`, select an exact tag or commit, and create `backup/pre-replay-TARGET` from the current fork head.
2. Create a non-`main` replay branch from the exact upstream target ref.
3. Copy this ledger and the fork contract in `AGENTS.md` onto the replay branch before runtime code.
4. Re-evaluate every seam against the target and classify it as runtime carry, partial-overlap carry, support/proof carry, absorbed upstream, drop candidate, or pending triage.
5. Reimplement required behavior in dependency order: model/config foundations, Codex transport, deriver schema/state machine, MCP shared core and transports, deployment policy, then support tooling.
6. Run each seam's focused proof. Perform deferred live proof only on the authorized deployment surface.
7. Update evidence, absorption decisions, source/target references, and replay summary before merging.

Never replay the fork by rebasing or cherry-picking its historical commit series wholesale. Never perform an upgrade replay directly on `main`.

## Narrow validation set

- `python /home/bex/.agents/skills/fork-repo/scripts/validate_fork_ledger.py --repo /home/bex/honcho`
- `.venv/bin/pytest -q tests/llm/test_backends/test_codex.py tests/llm/test_codex_auth.py tests/llm/test_codex_registry.py tests/llm/test_codex_tool_loop.py tests/llm/test_credentials.py tests/llm/test_model_config.py tests/llm/test_telemetry_llm_call.py`
- `python -m py_compile src/deriver/retry_policy.py src/deriver/dead_letter.py src/deriver/queue_manager.py migrations/versions/33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py`
- `(cd mcp && bun run typecheck:node && bun test)`
- `python -m py_compile scripts/import_openclaw_private_memory.py scripts/watch_dream_health.py`
- `python -c 'import tomllib; tomllib.load(open("config.toml", "rb")); print("toml-ok")'`
- `docker compose -f docker-compose.yml.example -f docker-compose.override.yml config --quiet`
- `git diff --check`

## Deferred live proof

- Codex subscription calls require the mounted auth file and active subscription endpoint.
- Deriver retry/dead-letter proof requires a controlled live queue item and database inspection.
- The deriver pytest/migration lane requires a disposable Postgres endpoint reachable through `DB_CONNECTION_URI`; the tracked Compose override deliberately does not publish the live database on the host.
- Public MCP proof requires Cloudflare, OAuth, the user service, and a real ChatGPT connector call.
- Stdio proof requires the installed local Codex MCP configuration.
- Memory repair scripts require a verified snapshot, explicit preflight counts, and disposable rehearsal because each script commits its own transaction; import scripts require an explicitly selected disposable workspace.
- Production config proof requires intentional container restart followed by running-settings and OpenClaw status read-back.
