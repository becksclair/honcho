# Fork Ledger

Schema: `3`
Upstream remote: `upstream`
Upstream ref: `upstream/main`
Current upstream base: `602347d76c4e5e464e883f5df32b5bad5aa7952b`
Current upstream label: `v3.0.11`
Last upgrade record: `none`

## Fork support paths

- `AGENTS.md`
- `.github/workflows/unified-tests.yml`
- `.github/workflows/unittest.yml`

## Active seams

### bundled-agent-skills

Kind: `proof`
Criticality: `medium`

Contract:
The repository carries versioned agent instructions for inspecting Honcho, integrating it into applications, and migrating the Python and TypeScript SDKs.

Upstream gap:
The recorded upstream base does not contain these local agent skills or their integration/migration references.

Absorbed when:
The local agent workflows move to another maintained source or upstream ships equivalent repository-versioned guidance and the local copies are no longer needed.

Upstream anchors:
- `.agents/skills`
- `pyproject.toml`
- `mcp/package.json`

Fork touchpoints:
- `.agents/skills/honcho-cli/**`
- `.agents/skills/honcho-integration/**`
- `.agents/skills/migrate-honcho-py/**`
- `.agents/skills/migrate-honcho-ts/**`

Proof:
- Fork manual: verify each bundled skill has a readable `SKILL.md`, its relative references resolve, and examples match the replayed Honcho SDK versions.
- Upstream manual: inspect the same `.agents/skills` paths on the recorded upstream base.
- Upstream expected: the local Honcho inspection/integration/migration skills are absent.
- Fork expected: all four skill families and their referenced support files are present and internally coherent.

Consumers:
- Local coding agents working in or against this Honcho fork.

Replay notes:
- The duplicated historical `migrate skills` commits are one behavioral seam, not two carries.

### codex-subscription-transport

Kind: `integration`
Criticality: `critical`

Contract:
Honcho can use a Codex/ChatGPT subscription as an LLM transport without an OpenAI API key, including auth-file loading/refresh, streaming Responses calls, structured output, tool calls, concurrency/retry behavior, telemetry, and normal model registry/config plumbing.

Upstream gap:
The recorded upstream base has no subscription-authenticated Codex transport, Codex auth-file lifecycle, registry entry, or corresponding focused/live proof.

Absorbed when:
Upstream provides an equivalent subscription-authenticated Codex transport and the focused plus live subscription tests pass without the fork implementation.

Upstream anchors:
- `src/llm/registry.py::get_backend`
- `src/llm/backends/codex.py::CodexResponsesBackend`
- `src/llm/codex_auth.py::CodexAuthManager`
- `src/config.py::ModelTransport`

Fork touchpoints:
- `ROADMAP.md`
- `src/config.py`
- `src/llm/__init__.py`
- `src/llm/backends/codex.py`
- `src/llm/codex_auth.py`
- `src/llm/credentials.py`
- `src/llm/registry.py`
- `src/llm/types.py`
- `tests/codex_test_helpers.py`
- `tests/live_llm/test_live_codex.py`
- `tests/llm/test_backends/test_codex.py`
- `tests/llm/test_codex_auth.py`
- `tests/llm/test_codex_registry.py`
- `tests/llm/test_codex_tool_loop.py`
- `tests/llm/test_credentials.py`
- `tests/llm/test_model_config.py`
- `tests/llm/test_telemetry_llm_call.py`

Proof:
- Fork command: `.venv/bin/pytest -q tests/llm/test_backends/test_codex.py tests/llm/test_codex_auth.py tests/llm/test_codex_registry.py tests/llm/test_codex_tool_loop.py tests/llm/test_credentials.py tests/llm/test_model_config.py tests/llm/test_telemetry_llm_call.py`.
- Upstream manual: inspect `src/llm` transport registration, credentials, and backend implementations on the recorded upstream base.
- Upstream expected: no Codex subscription backend/auth lifecycle exists.
- Fork expected: focused Codex transport/config/auth/tool-loop tests pass; live proof additionally succeeds only when explicitly run with the mounted subscription auth file.

Consumers:
- Honcho LLM runtime paths configured with `transport = "codex"`.

Replay notes:
- Keep credential selection provider-neutral and never place subscription tokens in config, logs, tests, or commits.

### gpt56-responses-lite

Kind: `integration`
Criticality: `critical`

Contract:
GPT-5.6 Codex models use the Responses-Lite request contract, including the protocol header, developer/input item shape, additional tools, encrypted reasoning context, and supported Codex client identity.

Upstream gap:
The recorded upstream base has neither the GPT-5.6 model-specific Responses-Lite branch nor its protocol/request-shape tests.

Absorbed when:
The active subscription endpoint accepts upstream's request shape for GPT-5.6 and the fork's structured-output/tool-loop proof passes with the model-specific branch removed.

Upstream anchors:
- `src/llm/backends/codex.py::_uses_responses_lite`
- `src/llm/backends/codex.py::CodexResponsesClient._headers`
- `src/llm/backends/codex.py::CodexResponsesBackend._build_request_body`

Fork touchpoints:
- `src/llm/backends/codex.py`
- `src/config.py`
- `config.toml`
- `tests/llm/test_backends/test_codex.py`
- `tests/llm/test_codex_tool_loop.py`

Proof:
- Fork command: `.venv/bin/pytest -q tests/llm/test_backends/test_codex.py tests/llm/test_codex_tool_loop.py`.
- Upstream manual: inspect the Codex request builder and GPT-5.6 handling on the recorded upstream base.
- Upstream expected: there is no Responses-Lite model branch or corresponding protocol header/request contract.
- Fork expected: GPT-5.6 request-shape and tool-loop tests pass; live structured/tool proof succeeds when explicitly authorized.

Consumers:
- GPT-5.6 models reached through the Codex subscription transport.

Replay notes:
- Re-check the live subscription protocol and accepted model identity on every upgrade; this seam is intentionally separable from the base Codex transport.

### deriver-retry-durability

Kind: `runtime`
Criticality: `critical`

Contract:
Transient deriver failures are retried with bounded backoff; terminal/exhausted poison work is independently dead-lettered and replayable; later queue work is not discarded; a successful retry clears stale error/deadline state while preserving attempt history.

Upstream gap:
The recorded upstream base does not provide the fork's durable retry columns/state machine, poison-item archive/replay behavior, or successful-retry state cleanup.

Absorbed when:
Upstream provides equivalent durable scheduling, poison isolation, dead-letter retention/replay, telemetry, and successful-retry cleanup semantics.

Upstream anchors:
- `src/deriver/queue_manager.py::QueueManager`
- `src/deriver/retry_policy.py`
- `src/deriver/dead_letter.py`
- `src/models.py::QueueItem`

Fork touchpoints:
- `docs/plans/deriver-retry-durability.md`
- `migrations/versions/33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py`
- `src/config.py`
- `src/deriver/dead_letter.py`
- `src/deriver/queue_manager.py`
- `src/deriver/retry_policy.py`
- `src/models.py`
- `src/telemetry/events/representation.py`
- `tests/alembic/revisions/__init__.py`
- `tests/alembic/revisions/test_33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py`
- `tests/conftest.py`
- `tests/deriver/test_queue_retry.py`
- `tests/deriver/test_retry_policy.py`

Proof:
- Fork command: `python -m py_compile src/deriver/retry_policy.py src/deriver/dead_letter.py src/deriver/queue_manager.py migrations/versions/33bb37bb59ee_add_queue_retry_columns_and_dead_letter_.py`.
- Upstream manual: inspect queue failure processing, retry metadata, dead-letter persistence, and successful completion behavior on the recorded upstream base.
- Upstream expected: failures do not have the same bounded retry/dead-letter/replay state machine and successful retry cleanup.
- Fork expected: compile proof passes; the isolated PostgreSQL retry/migration suite passes and a successful retry clears `error`/`next_attempt_at` without resetting `attempts`.

Consumers:
- Background deriver queue processing and operators replaying terminal failures.

Replay notes:
- Migration, ORM state, claim scheduling, transitions, replay, telemetry, and cleanup are one invariant frontier; never carry only the Python control flow.

### production-model-policy

Kind: `build`
Criticality: `high`

Contract:
The self-hosted deployment carries explicit model/transport profiles and live policy for effort, tier, concurrency, retries, embeddings, and fallback behavior, while keeping operator backup files out of Git.

Upstream gap:
The recorded upstream base does not contain these local production profiles or the deployment-specific model routing policy.

Absorbed when:
The deployment no longer needs repository-tracked local model policy/profiles, or another authoritative deployment source fully owns the same settings.

Upstream anchors:
- `config.toml`
- `src/config.py::Settings`
- `docker-compose.yml.example`

Fork touchpoints:
- `.gitignore`
- `config.toml`
- `config-codex-primary.toml`
- `config-deepseek-free-primary.toml`
- `config-deepseek-primary.toml`

Proof:
- Fork command: `python -c 'import tomllib; [tomllib.load(open(p,"rb")) for p in ("config.toml","config-codex-primary.toml","config-deepseek-free-primary.toml","config-deepseek-primary.toml")]; print("toml-ok")'`.
- Upstream manual: inspect production/local model profiles and the equivalent settings on the recorded upstream base.
- Upstream expected: the fork's model profiles and deployment policy are absent.
- Fork expected: every tracked profile parses and the selected deployment profile maps to valid Honcho model configuration.

Consumers:
- Saga/Asgard self-hosted Honcho deployment configuration.

Replay notes:
- Merge new upstream settings field-by-field; keep secrets outside Git and do not treat a model-provider default as equivalent to this deployment policy.

### local-compose-port-isolation

Kind: `build`
Criticality: `medium`

Contract:
Honcho PostgreSQL remains private to the Compose network and does not claim host port 5432.

Upstream gap:
The recorded upstream base publishes the database port in a way that conflicts with another local service owning localhost:5432.

Absorbed when:
The base Compose configuration no longer publishes the database port or the local port ownership constraint disappears.

Upstream anchors:
- `docker-compose.yml.example::services.database`
- `docker-compose.override.yml::services.database.ports`

Fork touchpoints:
- `docker-compose.override.yml`

Proof:
- Fork command: `docker compose -f docker-compose.yml.example -f docker-compose.override.yml config --format json | python -c 'import json,sys; p=json.load(sys.stdin)["services"]["database"].get("ports", []); assert p == [], p; print("database-ports-ok")'`.
- Upstream manual: inspect the database service port publication on the recorded upstream base without the fork override.
- Upstream expected: the base configuration exposes a host database port.
- Fork expected: the merged Compose model has no database host ports.

Consumers:
- Local deployments where another service owns host port 5432.

### private-memory-importer

Kind: `proof`
Criticality: `medium`

Contract:
OpenClaw private-memory exports can be normalized and replayed into an explicitly selected Honcho workspace without manual per-record API work.

Upstream gap:
The recorded upstream base does not include a compatible importer for the local OpenClaw private-memory export format and identity conventions.

Absorbed when:
Upstream or another maintained tool supports the same export format, normalization, identity mapping, and safe target selection.

Upstream anchors:
- `scripts/import_openclaw_private_memory.py`
- `src/routers/workspaces.py`
- `src/routers/peers.py`

Fork touchpoints:
- `scripts/import_openclaw_private_memory.py`

Proof:
- Fork command: `python -m py_compile scripts/import_openclaw_private_memory.py`.
- Upstream manual: inspect import/migration scripts on the recorded upstream base for this OpenClaw export contract.
- Upstream expected: no equivalent importer exists.
- Fork expected: the importer compiles and its behavioral proof can run against a disposable workspace without touching production memory.

Consumers:
- Explicit operator-driven private-memory migrations.

Replay notes:
- Never replay production memory merely because this file survives an upstream upgrade.

### dream-health-watcher

Kind: `proof`
Criticality: `high`

Contract:
Operators can reduce dream/replay queue and log state to concise health output with stable 0/1/2 exit codes and bounded best-effort redaction of common sensitive identifiers.

Upstream gap:
The recorded upstream base lacks this local queue/log health watcher and its operator-facing exit-code contract.

Absorbed when:
Upstream or deployment observability provides equivalent concise dream/replay health evidence and the local watcher is no longer consumed.

Upstream anchors:
- `scripts/watch_dream_health.py`
- `src/dreamer`
- `src/deriver`

Fork touchpoints:
- `scripts/watch_dream_health.py`

Proof:
- Fork command: `python -m py_compile scripts/watch_dream_health.py`.
- Upstream manual: inspect dream/deriver operational tooling on the recorded upstream base.
- Upstream expected: no equivalent watcher with the local 0/1/2 summary contract exists.
- Fork expected: the watcher compiles; authorized live proof preserves exit-code meanings and known redactors while treating snippets as sensitive output.

Consumers:
- Honcho operators diagnosing dream and replay health.

### chatgpt-oauth-mcp

Kind: `integration`
Criticality: `critical`

Contract:
The Node MCP deployment exposes persistent proxy-aware Streamable HTTP with owner-password OAuth, DCR, PKCE, exact redirect validation, refresh/revocation, hashed token persistence, server-side Honcho authorization, and rejection of unsupported standalone SSE streams.

Upstream gap:
The recorded upstream base does not provide this Node HTTP/OAuth deployment, its proxy/public-URL security policy, persisted OAuth lifecycle, or transport tests.

Absorbed when:
Upstream ships an equivalent deployable HTTP/OAuth MCP whose security, identity, transport, persistence, and proxy contracts pass the fork's focused/integration proof.

Upstream anchors:
- `mcp/src/http.ts::createHttpServer`
- `mcp/src/oauth-provider.ts`
- `mcp/src/oauth-store.ts`
- `mcp/src/node-config.ts`

Fork touchpoints:
- `mcp/.gitignore`
- `mcp/Dockerfile.saga`
- `mcp/README.md`
- `mcp/bun.lock`
- `mcp/package.json`
- `mcp/src/http.test.ts`
- `mcp/src/http.ts`
- `mcp/src/node-config.test.ts`
- `mcp/src/node-config.ts`
- `mcp/src/oauth-provider.ts`
- `mcp/src/oauth-store.test.ts`
- `mcp/src/oauth-store.ts`
- `mcp/src/server.ts`
- `mcp/tsconfig.json`
- `mcp/tsconfig.node.json`

Proof:
- Fork command: `cd mcp && bun run typecheck:node && bun test`.
- Upstream manual: inspect the Node MCP transport/auth/deployment surface on the recorded upstream base.
- Upstream expected: the persistent HTTP/OAuth deployment and its security/transport contract are absent.
- Fork expected: Node typecheck/tests pass, standalone SSE is rejected, and authorized integration proof survives OAuth token refresh/restart behind the configured proxy.

Consumers:
- The public ChatGPT-facing Honcho MCP deployment.

Replay notes:
- Preserve one-hop proxy trust, public HTTPS metadata, registration hostname allowlisting, exact registered redirect equality, token hashing, and server-side Honcho authorization.

### mcp-peer-identity-normalization

Kind: `integration`
Criticality: `critical`

Contract:
Historical ChatGPT/user peer aliases are canonicalized at every shared MCP write boundary, while deployment configuration fixes trusted human/assistant defaults rather than letting callers redefine server identity.

Upstream gap:
The recorded upstream base accepts caller-provided peer IDs without the fork's configurable canonical aliases and fixed deployment identity boundary.

Absorbed when:
Upstream provides equivalent configurable canonical peer aliases plus server-fixed identity with coverage across peer/session/message write paths.

Upstream anchors:
- `mcp/src/peer-identity.ts::canonicalizePeerId`
- `mcp/src/tools/peers.ts`
- `mcp/src/tools/sessions.ts`
- `mcp/src/node-config.ts`

Fork touchpoints:
- `mcp/src/node-config.test.ts`
- `mcp/src/node-config.ts`
- `mcp/src/peer-identity.test.ts`
- `mcp/src/peer-identity.ts`
- `mcp/src/tools/peers.ts`
- `mcp/src/tools/sessions.ts`

Proof:
- Fork command: `cd mcp && bun test`.
- Upstream manual: inspect peer/session/message identity handling on the recorded upstream base.
- Upstream expected: historical aliases can reach shared write boundaries without canonicalization and caller identity is not fixed by the same deployment policy.
- Fork expected: alias/config tests pass and integration calls converge historical aliases to their canonical peer IDs.

Consumers:
- ChatGPT and other clients sharing the Node MCP tool layer.

### mcp-conclusion-query-controls

Kind: `integration`
Criticality: `high`

Contract:
MCP clients can list/query explicit, deductive, or inductive conclusions with level filtering, pagination/reverse controls, semantic distance constraints, and returned conclusion levels.

Upstream gap:
The recorded upstream base MCP conclusion tools expose a narrower list/query surface without these local controls/result metadata.

Absorbed when:
Upstream MCP tools expose equivalent filtering, pagination/order, distance, and level result contracts.

Upstream anchors:
- `mcp/src/tools/conclusions.ts`
- `src/routers/conclusions.py`

Fork touchpoints:
- `mcp/src/tools/conclusions.ts`

Proof:
- Fork command: `cd mcp && bun run typecheck:node`.
- Upstream manual: inspect MCP conclusion list/query arguments and result fields on the recorded upstream base.
- Upstream expected: one or more level/pagination/distance/order controls and returned-level metadata are absent.
- Fork expected: Node typecheck passes and disposable/read-only integration proof returns the requested conclusion levels and controls.

Consumers:
- MCP clients inspecting Honcho conclusions directly.

### local-codex-stdio-mcp

Kind: `integration`
Criticality: `high`

Contract:
Standalone Codex can connect through a local stdio MCP wrapper with its own fixed workspace/peer identity, independent of the public ChatGPT OAuth identity boundary.

Upstream gap:
The recorded upstream base has no local stdio entrypoint/wrapper carrying the separate Codex identity convention.

Absorbed when:
The local client moves to another transport with the same identity isolation and live tool proof, or upstream provides an equivalent stdio deployment surface.

Upstream anchors:
- `mcp/src/stdio.ts`
- `mcp/src/server.ts`
- `mcp/honcho-mcp-stdio`

Fork touchpoints:
- `mcp/honcho-mcp-stdio`
- `mcp/src/server.ts`
- `mcp/src/stdio.ts`

Proof:
- Fork command: `cd mcp && bun run typecheck:node`.
- Upstream manual: inspect the MCP entrypoints on the recorded upstream base.
- Upstream expected: no local stdio wrapper with the fork's separate Codex identity exists.
- Fork expected: Node typecheck passes and the private stdio smoke returns server instructions/tool inventory under the intended Codex identity.

Consumers:
- Standalone Codex clients configured for the local Honcho MCP.

### mcp-identity-data-hygiene

Kind: `proof`
Criticality: `medium`

Contract:
Reviewed opt-in SQL repairs can merge historical peer aliases and canonicalize legacy peer-card/display/memory text without being run automatically during service startup or fork upgrades.

Upstream gap:
The recorded upstream base has no repair scripts for these local historical identity/data invariants.

Absorbed when:
Each relevant data invariant is permanently enforced and no maintained environment needs the corresponding historical repair.

Upstream anchors:
- `mcp/src/peer-identity.ts`
- `src/identity.py`
- `src/models.py`

Fork touchpoints:
- `mcp/scripts/canonicalize-sarah-bex-peer-card.sql`
- `mcp/scripts/merge-chatgpt-peer-aliases.sql`
- `mcp/scripts/replace-rebecca-clair-display-name.sql`
- `mcp/scripts/replace-rebecca-clair-in-memory.sql`

Proof:
- Fork manual: restore a disposable database snapshot, capture preflight counts, run exactly one reviewed repair, inspect post-state/invariants, then discard the database.
- Upstream manual: inspect identity/data repair tooling on the recorded upstream base.
- Upstream expected: these local historical repair operations are absent.
- Fork expected: rehearsed scripts converge the intended historical records without being part of automatic startup/migration execution.

Consumers:
- Explicit operator-driven repair of historical Honcho data.

Replay notes:
- The scripts own their transactions; treat every real invocation as destructive and require a verified backup plus disposable rehearsal.

### workspace-collective-recall-and-identity

Kind: `runtime`
Criticality: `critical`

Contract:
A workspace can declare canonical peer aliases and strict identity policy, and Dialectic retrieval can produce one globally ranked evidence pool across configured observers while preserving observer provenance, session-safe adjacency, observer-local writes, and legacy behavior when identity policy is absent.

Upstream gap:
The recorded upstream base isolates recall to one observer and lacks the fork's workspace identity schema/enforcement plus cross-observer retrieval/ranking contract.

Absorbed when:
Upstream provides equivalent configurable canonical identity enforcement and globally ranked multi-observer recall with the same write isolation, provenance, and backward-compatibility behavior.

Upstream anchors:
- `src/schemas/configuration.py::WorkspaceConfiguration`
- `src/identity.py`
- `src/dialectic/retrieval.py`
- `src/dialectic/core.py`
- `src/security.py`

Fork touchpoints:
- `mcp/src/peer-identity.test.ts`
- `mcp/src/peer-identity.ts`
- `src/crud/__init__.py`
- `src/crud/document.py`
- `src/crud/message.py`
- `src/crud/peer.py`
- `src/crud/session.py`
- `src/dialectic/chat.py`
- `src/dialectic/core.py`
- `src/dialectic/prompts.py`
- `src/dialectic/retrieval.py`
- `src/identity.py`
- `src/routers/conclusions.py`
- `src/routers/keys.py`
- `src/routers/peers.py`
- `src/routers/sessions.py`
- `src/routers/workspaces.py`
- `src/schemas/__init__.py`
- `src/schemas/configuration.py`
- `src/security.py`
- `src/utils/agent_tools.py`
- `src/utils/config_helpers.py`
- `tests/dialectic/test_collective_recall.py`
- `tests/test_identity.py`
- `tests/test_schema_validations.py`
- `tests/utils/test_agent_tools.py`
- `tests/utils/test_config_helpers.py`

Proof:
- Fork command: `.venv/bin/pytest -q tests/test_identity.py tests/dialectic/test_collective_recall.py tests/test_schema_validations.py tests/utils/test_agent_tools.py tests/utils/test_config_helpers.py`.
- Upstream manual: inspect workspace identity configuration and Dialectic observer/retrieval scope on the recorded upstream base.
- Upstream expected: there is no equivalent strict canonical identity policy plus globally ranked cross-observer recall path.
- Fork expected: focused identity/collective-recall tests pass, legacy workspaces retain preserve-case/dynamic behavior, and evidence preserves observer provenance.

Consumers:
- Family/shared-memory workspaces and Dialectic callers relying on collective recall.

Replay notes:
- Keep writes observer-local; widen only read-time retrieval. The deferred CRUD import in `src/dialectic/retrieval.py` is part of avoiding the import cycle created by this seam.

### fam-workspace-migration

Kind: `integration`
Criticality: `critical`

Contract:
A reviewed manifest-driven operation can transactionally converge selected source workspaces into the canonical family workspace while preserving IDs, embeddings, reasoning/queue provenance, authoritative peer cards, excluded archives, failure state, rollback gates, and post-migration verification.

Upstream gap:
The recorded upstream base has no migration operation for this local multi-workspace/identity convergence contract.

Absorbed when:
The migration is permanently retired after every maintained environment has converged, or upstream provides an equivalent guarded migration/verification path for the same data model.

Upstream anchors:
- `src/operations/fam_migration.py`
- `src/models.py`
- `src/identity.py`
- `src/schemas/configuration.py`

Fork touchpoints:
- `scripts/migrate_fam_workspace.py`
- `src/operations/__init__.py`
- `src/operations/fam_migration.py`
- `tests/operations/test_fam_migration.py`

Proof:
- Fork command: `.venv/bin/pytest -q tests/operations/test_fam_migration.py`.
- Upstream manual: inspect workspace/identity migration operations on the recorded upstream base.
- Upstream expected: there is no equivalent manifest-driven guarded convergence operation.
- Fork expected: focused tests pass and disposable-PostgreSQL rehearsal preserves IDs/provenance/failure state while satisfying final verification gates.

Consumers:
- Explicit operator-driven migration of maintained Honcho workspaces into the family workspace.

Replay notes:
- Never execute during ordinary startup or replay. Real use requires stopped writers, verified full-database backup, current production-data preflight, and post-cutover verification.

### deepseek-specialist-iteration-budgets

Kind: `runtime`
Criticality: `high`

Contract:
Dream specialists have larger bounded tool-loop iteration budgets so DeepSeek-backed deduction/induction can complete valid multi-step work without premature loop exhaustion.

Upstream gap:
The recorded upstream base uses lower Base/Deduction/Induction specialist iteration caps that are insufficient for the deployment's observed DeepSeek tool loops.

Absorbed when:
Upstream exposes suitable configurable iteration budgets or the deployed models reliably complete within upstream defaults and the local overrides can be removed.

Upstream anchors:
- `src/dreamer/specialists.py::BaseSpecialist.get_max_iterations`
- `src/dreamer/specialists.py::DeductionSpecialist.get_max_iterations`
- `src/dreamer/specialists.py::InductionSpecialist.get_max_iterations`

Fork touchpoints:
- `src/dreamer/specialists.py`

Proof:
- Fork manual: inspect the three specialist iteration caps and exercise bounded dream deduction/induction with the deployment model while watching for tool-loop cap warnings.
- Upstream manual: inspect the same specialist caps on the recorded upstream base.
- Upstream expected: Base/Deduction/Induction caps are 15/12/10 rather than the fork's 20/20/15.
- Fork expected: configured specialists expose 20/20/15 and representative DeepSeek dream work completes within those bounds.

Consumers:
- Dream deduction and induction paths using tool-capable DeepSeek models.

### container-source-permissions

Kind: `build`
Criticality: `medium`

Contract:
The production container normalizes ownership/permissions of copied source so the runtime user can reliably read and execute application files regardless of host checkout metadata.

Upstream gap:
The recorded upstream base copies source without the fork's explicit post-copy permission normalization.

Absorbed when:
Upstream's image build guarantees equivalent runtime-readable/executable source permissions without the fork commands.

Upstream anchors:
- `Dockerfile`
- `Dockerfile::COPY`

Fork touchpoints:
- `Dockerfile`

Proof:
- Fork manual: build the image from a checkout with restrictive-but-valid source metadata and inspect application source ownership/modes as the runtime user.
- Upstream manual: inspect post-copy permission handling in the recorded upstream Dockerfile.
- Upstream expected: no equivalent source permission normalization is present.
- Fork expected: application source is readable/executable by the configured runtime user after image build.

Consumers:
- Self-hosted API/deriver container images.

### openai-reasoning-history-replay

Kind: `integration`
Criticality: `high`

Contract:
OpenAI-compatible tool loops preserve provider-native assistant reasoning history: structured `reasoning_details` takes precedence when present, otherwise `thinking_content` is replayed as `reasoning_content` on the next tool turn.

Upstream gap:
The recorded upstream base drops provider-native plain reasoning content when rebuilding assistant tool-call history.

Absorbed when:
Upstream history/tool-loop adapters preserve the same reasoning payload precedence and DeepSeek/OpenAI-compatible multi-turn tool calls succeed without this fork path.

Upstream anchors:
- `src/llm/history_adapters.py::OpenAIHistoryAdapter.format_assistant_tool_message`
- `src/llm/tool_loop.py::format_assistant_tool_message`
- `src/llm/tool_loop.py::execute_tool_loop`

Fork touchpoints:
- `src/llm/history_adapters.py`
- `src/llm/tool_loop.py`
- `tests/llm/test_history_adapters.py`

Proof:
- Fork command: `.venv/bin/pytest -q tests/llm/test_history_adapters.py`.
- Upstream manual: inspect assistant tool-call history reconstruction on the recorded upstream base with `thinking_content` but no `reasoning_details`.
- Upstream expected: provider-native reasoning content is not replayed on the next tool turn.
- Fork expected: focused history-adapter tests preserve `reasoning_details` precedence and emit plain `reasoning_content` otherwise.

Consumers:
- OpenAI-compatible reasoning providers, notably DeepSeek, during multi-turn tool execution.

### conclusion-level-compatibility-alias

Kind: `runtime`
Criticality: `medium`

Contract:
The public conclusion API exposes the stable `level` field together with a backward-compatible `conclusion_type` alias, and current fork schemas preserve the compatibility field expected by existing consumers.

Upstream gap:
The recorded upstream base serializes conclusion level without the fork's `conclusion_type` compatibility alias.

Absorbed when:
All maintained consumers use the stable `level` field or upstream provides the same compatibility alias and route serialization contract.

Upstream anchors:
- `src/routers/conclusions.py::_serialize_conclusion`
- `src/schemas/api.py::Conclusion`
- `src/schemas/api.py::Workspace`

Fork touchpoints:
- `src/routers/conclusions.py`
- `src/schemas/api.py`
- `tests/routes/test_conclusions.py`

Proof:
- Fork command: `.venv/bin/pytest -q tests/routes/test_conclusions.py`.
- Upstream manual: inspect conclusion response serialization and schema fields on the recorded upstream base.
- Upstream expected: responses do not include `conclusion_type` alongside `level`.
- Fork expected: conclusion route tests return matching `level` and `conclusion_type` values.

Consumers:
- Clients that still read the historical `conclusion_type` field.

Replay notes:
- Re-check whether `Workspace.conclusion_type` remains a real consumer contract on the next upgrade; the route proof primarily exercises conclusion responses.

### test-database-url-fidelity

Kind: `proof`
Criticality: `low`

Contract:
The async test database helper preserves the real password when rendering SQLAlchemy URL objects instead of passing a redacted `***` password string to the engine.

Upstream gap:
The recorded upstream base uses `str(db_url)`, whose SQLAlchemy string representation hides passwords and can make non-empty-password test database connections invalid.

Absorbed when:
Upstream passes the URL object directly or otherwise renders it with credentials intact before creating the async engine.

Upstream anchors:
- `tests/conftest.py::setup_test_database`
- `sqlalchemy.engine.URL.render_as_string`

Fork touchpoints:
- `tests/conftest.py`

Proof:
- Fork command: `python -c 'from sqlalchemy.engine import make_url; u=make_url("postgresql+asyncpg://u:secret@localhost/db"); assert "***" in str(u); assert "secret" in u.render_as_string(hide_password=False); print("url-fidelity-ok")'`.
- Upstream manual: inspect `setup_test_database` on the recorded upstream base.
- Upstream expected: the helper converts the URL through the password-redacting `str()` representation.
- Fork expected: the helper passes a rendered connection string containing the actual encoded credential to `create_async_engine`.

Consumers:
- Test suites using non-empty PostgreSQL passwords.
