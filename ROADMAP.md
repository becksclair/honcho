# Integrate ChatGPT Codex Responses as a Honcho LLM Transport

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This plan follows `/home/bex/.agents/PLANS.md`. It is intentionally self-contained: a future agent or human should be able to start with this file, the current repository, and the local machine state, then implement and verify the integration without reading the prior chat.

## Purpose / Big Picture

Honcho currently uses provider backends such as OpenAI-compatible chat completions, Anthropic, and Gemini through `src/llm/`. The goal of this work is to add a fourth model transport, `codex`, that lets Honcho call `gpt-5.5` through Bex's ChatGPT subscription while preserving Honcho's normal model-tool semantics.

The implementation is an OpenAI Responses-style backend over the ChatGPT Codex endpoint:

    https://chatgpt.com/backend-api/codex/responses

After this change, a Honcho operator should be able to configure:

    transport = "codex"
    model = "gpt-5.5"

and have both ordinary structured-output calls and Honcho's existing tool loop work through the same `honcho_llm_call` entrypoint. Codex should emit function-call requests; Honcho should execute the tools; Honcho should append tool results; the model should continue or stop exactly as it does with the other providers.

The user-visible proof at the end is not merely that tests pass. The proof is that at least one bounded Honcho tool-loop call completes through `transport="codex"`, executes a real Honcho tool through the existing executor path, receives a final model answer, records token usage, and does not expose auth tokens or private memory content in logs.

## Progress

- [x] (2026-06-05) Inspected the existing Honcho LLM architecture and confirmed model transports are currently limited to `anthropic`, `openai`, and `gemini` in `src/config.py`.
- [x] (2026-06-05) Confirmed the existing OpenAI backend already handles GPT-5-family token naming for chat completions, but direct OpenAI API use is separate from using a ChatGPT subscription through Codex.
- [x] (2026-06-05) Verified the normal Codex install is logged in with ChatGPT and can list `gpt-5.5`.
- [x] (2026-06-06) Confirmed the local implementation can reuse Codex's normal ChatGPT auth file at `/home/bex/.codex/auth.json`.
- [x] (2026-06-06) Proved the local auth file shape without printing tokens. `/home/bex/.codex/auth.json` contains `tokens.access_token`, `tokens.refresh_token`, `tokens.account_id`, and `tokens.id_token`.
- [x] (2026-06-06) Confirmed Honcho already has `openai>=1.99.7` in `pyproject.toml`, and the locked environment has OpenAI Python `2.36.0` with `AsyncOpenAI.responses.create`.
- [x] (2026-06-06) Confirmed the installed OpenAI Python Responses client supports `tools`, `tool_choice`, and `parallel_tool_calls`.
- [x] (2026-06-06) Researched OpenClaw/Pi's ChatGPT Codex `gpt-5.5` path and confirmed it calls the ChatGPT Codex backend as an OpenAI Responses-family provider.
- [x] (2026-06-06) Removed the SDK/CLI experiment path from the implementation plan because the user needs normal tool-call semantics immediately.
- [x] (2026-06-06) Extended Honcho config types to support `transport="codex"`, `codex/gpt-5.5` shorthand, and `LLM_CODEX_*` settings.
- [x] (2026-06-06) Added `src/llm/codex_auth.py` for ChatGPT Codex auth loading, JWT expiry checks, OAuth refresh, atomic mode-600 writes, and sanitized failures.
- [x] (2026-06-06) Added `CodexResponsesBackend` in `src/llm/backends/codex.py` using direct `httpx` calls to the ChatGPT Codex Responses endpoint.
- [x] (2026-06-06) Converted Honcho messages, assistant tool calls, and tool results into Responses input items, including `function_call` and `function_call_output`.
- [x] (2026-06-06) Converted Honcho tools into Responses function tools and mapped `tool_choice` including `any` to `required`.
- [x] (2026-06-06) Normalized streamed Responses text, function calls, usage, and finish status into `CompletionResult`.
- [x] (2026-06-06) Wired `codex` into `src/llm/registry.py`, `src/llm/types.py`, `src/llm/credentials.py`, and provider history adapter selection.
- [x] (2026-06-06) Added non-live tests for config shorthand, credentials, auth loading/refresh, request construction, streaming aggregation, backend normalization, registry selection, tool-loop semantics, structured output, and telemetry.
- [x] (2026-06-06) Added and passed an explicitly marked live Responses test using configured `CODEX_AUTH_FILE`, `gpt-5.5`, and a harmless required Honcho tool call.
- [x] (2026-06-06) Ran a controlled live Honcho pilot by switching `dialectic.levels.minimal` to `codex/gpt-5.5`, restarting containers, and proving an in-container Honcho tool-loop call used the live config.
- [x] (2026-06-11) Mounted `/home/bex/.codex` into the API and deriver containers as `/run/codex-auth` and set `LLM_CODEX_AUTH_FILE=/run/codex-auth/auth.json`.
- [x] (2026-06-06) Rebuilt and recreated `honcho-api-1` and `honcho-deriver-1` so live containers use the new Codex backend code and auth mount.
- [x] (2026-06-06) Switched all dialectic levels (`minimal`, `low`, `medium`, `high`, `max`) to `transport="codex"` and `model="gpt-5.5"` after the minimal pilot was healthy.
- [x] (2026-06-06) Verified effective live config inside `honcho-api-1`: all dialectic levels use `codex/gpt-5.5`; deriver, summary, dream deduction, and dream induction remain on Kimi through the existing OpenAI-compatible route.
- [x] (2026-06-06) Passed in-container live-config smoke calls for `minimal` and `low`: each requested a tool, Honcho executed it, the final answer included the tool result, token counts were non-zero, and iterations were `2`.
- [x] (2026-06-06) Passed a real API-level dialectic smoke through `POST /v3/workspaces/openclaw/peers/codex/chat` with `reasoning_level="minimal"`: HTTP 200, content present, and the deployment marker was present without printing the answer body.
- [x] (2026-06-06) Ran post-rollout health checks: `/health` returned `{"status":"ok"}`, `openclaw honcho status` connected, queue had no active claims, and `scripts/watch_dream_health.py --since 15m` returned verdict `ok`.
- [x] (2026-06-06) Re-ran the focused non-live Codex suite from this plan with `HONCHO_CONFIG_TOML_DISABLED=1`: `20 passed`.
- [x] (2026-06-06) Re-ran `ruff check` and `basedpyright` on the touched Codex/config/test files: both clean.
- [x] (2026-06-06) Audited production files for forbidden Codex SDK/CLI/app-server wiring and found no runtime matches.
- [x] (2026-06-11) Removed the stale isolated-auth Docker boundary; live containers now read Codex auth from `/home/bex/.codex/auth.json`.

## Surprises & Discoveries

- Observation: The discarded SDK/CLI experiment should not remain as operational guidance.
  Evidence: The production backend now speaks the ChatGPT Codex Responses endpoint directly and preserves Honcho-owned tool execution. Live containers use the normal Codex auth file through the `/run/codex-auth/auth.json` mount.

- Observation: Honcho already owns the correct tool-loop abstraction.
  Evidence: `src/llm/tool_loop.py::execute_tool_loop` performs the iterative LLM call, executes requested tools through Honcho's executor closure, appends assistant/tool-result history, switches first-iteration `required`/`any` to `auto`, and records per-iteration telemetry.

- Observation: Codex can fit that loop as a Responses backend.
  Evidence: OpenClaw/Pi builds Codex requests with `store: false`, `stream: true`, `tool_choice: "auto"`, `parallel_tool_calls: true`, and Responses function tools, then parses `function_call` output items as tool calls.

- Observation: The existing OpenAI Python dependency is enough to start.
  Evidence: In the Honcho venv, `AsyncOpenAI.responses.create` exists and accepts `input`, `instructions`, `tools`, `tool_choice`, `parallel_tool_calls`, `stream`, `text`, `reasoning`, `prompt_cache_key`, `extra_headers`, and `timeout`.

- Observation: The ChatGPT Codex endpoint is Responses-shaped, not chat-completions-shaped.
  Evidence: OpenClaw normalizes Codex models to API `openai-codex-responses` and base URL `https://chatgpt.com/backend-api/codex`; Pi resolves requests to `/codex/responses`.

- Observation: The normal Codex auth file is the only supported local service auth source.
  Evidence: Direct Responses calls do not need the Codex binary, Codex app-server, Codex thread state, a working directory, or Codex prompt injection. The normal Codex install maintains the required ChatGPT auth shape at `/home/bex/.codex/auth.json`, and the running containers mount that directory directly.

- Observation: Token refresh is production work, not a later convenience.
  Evidence: The auth file has a refresh token. OpenClaw/Pi refreshes against `https://auth.openai.com/oauth/token` with client id `app_EMoamEEZ73f0CkXaXp7hrann` and OAuth grant type `refresh_token`.

- Observation: The ChatGPT Codex endpoint requires streaming and instructions.
  Evidence: Live probes returned sanitized HTTP 400 details for missing `instructions`, non-list `input`, `stream: false`, and unsupported output-token cap fields. A successful request used `instructions`, list-form Responses input items, and `stream: true`.

- Observation: Streamed Codex function calls arrive in `response.output_item.done` events even when `response.completed.response.output` is empty.
  Evidence: The live required-tool smoke initially returned no tool calls until the SSE parser merged `response.output_item.done` items back into the completed response before normalization.

- Observation: Container auth needed a user-boundary fix, not looser token permissions.
  Evidence: The first container probe resolved `LLM_CODEX_AUTH_FILE=/run/codex-auth/auth.json` but hit `PermissionError` because the image's internal `app` user could not read the host-owned mode-600 auth file. Running API and deriver as `${HONCHO_UID:-1000}:${HONCHO_GID:-1000}` preserved mode `600` while allowing access to the dedicated mounted auth directory.

## Decision Log

- Decision: Do not add or retain a Codex SDK dependency.
  Rationale: It adds packaging risk and does not preserve Honcho's tool loop in the first slice.
  Date/Author: 2026-06-06 / Codex

- Decision: Implement `transport="codex"` as a ChatGPT Codex Responses backend.
  Rationale: This is the path used by adjacent OpenClaw/Pi work for `openai-codex/gpt-5.5`, and it maps naturally onto Honcho's existing provider backend interface.
  Date/Author: 2026-06-06 / Codex

- Decision: Honcho remains the owner of tool execution.
  Rationale: Normal Honcho semantics require `execute_tool_loop` to call the model, execute tools, append tool results, switch `required` to `auto`, enforce iteration limits, and emit telemetry. Codex should only return function-call-shaped model output.
  Date/Author: 2026-06-06 / Codex

- Decision: Reuse existing dependencies first.
  Rationale: Honcho already depends on `openai` and `httpx`. Use `AsyncOpenAI.responses.create` if it can hit the ChatGPT backend cleanly; fall back to `httpx` only if the OpenAI Python client fights URL/header/SSE details.
  Date/Author: 2026-06-06 / Codex

- Decision: Reuse Codex's normal auth file for local service auth.
  Rationale: Bex already has Codex installed and logged in, so `/home/bex/.codex/auth.json` is the local source of truth. Docker mounts that directory into the containers and `CODEX_AUTH_FILE` points at the mounted `auth.json`.
  Date/Author: 2026-06-06 / Codex

- Decision: Do not let an external Codex agent runtime own tools in the first implementation.
  Rationale: That design would move tool-call ownership out of Honcho and require a separate request-handler bridge. It is a later native-Codex-tooling option, not the shortest path to normal Honcho semantics.
  Date/Author: 2026-06-06 / Codex

- Decision: Use direct `httpx` SSE aggregation for the production Codex backend.
  Rationale: The ChatGPT Codex endpoint requires `stream: true` and returns tool calls through Responses SSE item events. A small direct client keeps token refresh, custom headers, and event aggregation explicit without introducing external Codex agent runtime state.
  Date/Author: 2026-06-06 / Codex

- Decision: Move dialectic tool-loop paths to Codex first and leave deriver, summary, and dream paths on Kimi for now.
  Rationale: Dialectic is the native Honcho tool-loop surface and the live smoke proved Codex handles `function_call` / `function_call_output` there. Deriver, summary, and dream are higher-volume or structured/long-running memory paths; the live Codex endpoint rejected output cap fields, so those should move only after separate structured-output and cost/latency evidence.
  Date/Author: 2026-06-06 / Codex

## Outcomes & Retrospective

As of 2026-06-06, this document describes the Responses-first, tool-capable implementation. The code implementation now exists in the local worktree and the live `tests/live_llm/test_live_codex.py --live-llm -q -n 0` smoke has proved a real ChatGPT-backed `gpt-5.5` call can request a tool, let Honcho execute it, receive `function_call_output`, and finalize.

The live rollout has also completed for the dialectic tool-loop surface. `honcho-api-1` and `honcho-deriver-1` were rebuilt/recreated, the containers can read configured Codex auth without exposing token contents, all dialectic levels now use `codex/gpt-5.5`, and post-rollout health checks are green. The intentionally deferred paths are deriver, summary, dream deduction, and dream induction, which remain on Kimi until structured-output and output-cap behavior is proven safe for high-volume memory work.

The key lesson is that "Codex support" should mean a provider backend that speaks the model protocol while preserving Honcho's own loop, tools, validation, telemetry, and operational controls.

## Context and Orientation

Honcho is a Python FastAPI service with a background deriver worker. The live local deployment uses Docker Compose services named `honcho-api-1`, `honcho-deriver-1`, `honcho-database-1`, and `honcho-redis-1`. The live configuration file is `config.toml`, which is mounted into containers by `docker-compose.yml`.

An LLM transport is the implementation path Honcho uses to talk to a model provider. `src/config.py::ModelTransport` now accepts `anthropic`, `openai`, `gemini`, and `codex`. Model config objects flow from `config.toml` through Pydantic settings in `src/config.py`, into runtime model selection in `src/llm/runtime.py`, and then into `src/llm/api.py::honcho_llm_call`.

The public LLM entrypoint has two important paths:

- Tool-less calls eventually reach `src/llm/executor.py::honcho_llm_call_inner`, which delegates to a provider backend.
- Tool-loop calls use `src/llm/tool_loop.py::execute_tool_loop`, which calls the provider backend repeatedly and executes tools between model turns.

A provider backend is any object satisfying `src/llm/backend.py::ProviderBackend`. It must implement:

    complete(...)
    stream(...)

and return normalized `src/llm/backend.py::CompletionResult` objects with content, token counts, finish reason, tool calls, optional thinking data, and the raw provider response.

The existing backend implementations live in:

    src/llm/backends/openai.py
    src/llm/backends/anthropic.py
    src/llm/backends/gemini.py
    src/llm/backends/codex.py

The registry in `src/llm/registry.py` owns default clients, override clients, backend selection, and history adapters.

## Codex Responses Contract

The new `codex` transport should call the ChatGPT Codex endpoint in the same protocol family as OpenAI Responses.

Default base URL:

    https://chatgpt.com/backend-api/codex

Canonical request URL:

    https://chatgpt.com/backend-api/codex/responses

Required headers:

    Authorization: Bearer <access_token>
    chatgpt-account-id: <account_id>
    originator: honcho
    User-Agent: honcho
    OpenAI-Beta: responses=experimental

If the endpoint rejects `originator: honcho`, make `CODEX_ORIGINATOR` configurable and test the adjacent observed value `pi`. Do not hard-code another project's identity without recording why.

Default request fields:

    model: "gpt-5.5"
    store: false
    stream: true
    instructions: combined system/developer instructions, or a minimal default
    input: list-form Responses input items converted from Honcho messages
    text: {"verbosity": "low"}
    tool_choice: "auto" when tools are present and no stricter choice was requested
    parallel_tool_calls: true

Optional request fields:

    temperature
    service_tier
    prompt_cache_key
    reasoning
    include: ["reasoning.encrypted_content"]

Use `include: ["reasoning.encrypted_content"]` only if it is needed for response replay or follow-up turns. Keep the first implementation minimal if ordinary tool loops work without it.

## Message and Tool Mapping

The backend must translate Honcho's conversation messages into Responses input.

Initial user/system messages:

- `role="system"` and `role="developer"` should be combined into `instructions`.
- `role="user"` with string content becomes a Responses message with `input_text`.
- Non-string content should be handled only if existing Honcho callers produce it; otherwise reject with a clear validation error.

Assistant tool-call history:

- Reuse `OpenAIHistoryAdapter` at first unless tests prove a dedicated adapter is needed.
- Convert chat-completions-style assistant messages with `tool_calls` into Responses `function_call` items.
- When a tool call id is shaped as `<call_id>|<item_id>`, split it back into `call_id` and `id`.
- When only one id exists, use it as `call_id` and omit the Responses item id.

Tool result history:

- Convert `role="tool"` messages into Responses `function_call_output` items.
- Split `tool_call_id` the same way: use the left side as `call_id`.
- Serialize non-string tool results with `str(...)` unless the existing tool result is already structured in a provider-supported content shape.

Tool definitions:

Honcho tools generally arrive as:

    {
        "name": "...",
        "description": "...",
        "input_schema": {...}
    }

Convert them to Responses function tools:

    {
        "type": "function",
        "name": tool["name"],
        "description": tool["description"],
        "parameters": tool["input_schema"],
        "strict": None
    }

Do not force strict mode in the first implementation. Existing Honcho tool schemas may contain optional fields/defaults that strict OpenAI function calling rejects.

Tool choice mapping:

- `None` with tools present -> `"auto"`.
- `"auto"` -> `"auto"`.
- `"none"` -> `"none"`.
- `"required"` -> `"required"`.
- `"any"` -> `"required"` because Responses uses `required`, not `any`.
- Chat-completions forced function shape `{"type":"function","function":{"name":"x"}}` -> Responses forced function shape `{"type":"function","name":"x"}`.
- Unsupported tool-choice shapes should raise `ValidationException`.

This preserves Honcho's normal loop: the first iteration can require at least one tool call, and `execute_tool_loop` already switches `required`/`any` to `auto` after the first iteration.

## Response Normalization

Normalize Responses output into `CompletionResult`.

Text output:

- Concatenate `message` / `output_text` content into `CompletionResult.content`.
- For structured output, validate or repair using Honcho's existing structured-output helpers.

Function calls:

- For each `function_call`, parse JSON `arguments` into a dict.
- Emit `ToolCallResult(id=f"{call_id}|{item_id}" if both exist else call_id, name=name, input=args)`.
- If arguments are malformed, log the tool name and exception class only. Do not log raw arguments because they may contain user or memory content.

Finish reason:

- If any function calls are present, use a tool-call finish reason such as `"tool_calls"`.
- Otherwise map completed status to `"stop"`.
- Map incomplete, failed, cancelled, or errored statuses to provider errors that Honcho retry logic can catch.

Usage:

- Map `usage.input_tokens` to `input_tokens`.
- Map `usage.output_tokens` to `output_tokens`.
- Map `usage.input_tokens_details.cached_tokens` to `cache_read_input_tokens`.
- Use `0` when usage fields are unavailable.

Reasoning:

- If reasoning summaries are returned, map them into `thinking_content` or `reasoning_details` only if existing telemetry can handle them without leaking hidden reasoning or encrypted payloads.
- Do not log raw encrypted reasoning content.

## Auth Design

The local default auth source is Codex's normal ChatGPT login file:

    /home/bex/.codex/auth.json

The auth source must be configurable through `CODEX_AUTH_FILE`, and the local deployment points it at the mounted normal Codex auth file.

Observed shape:

    {
        "auth_mode": "chatgpt",
        "last_refresh": "...",
        "tokens": {
            "access_token": "...",
            "refresh_token": "...",
            "account_id": "...",
            "id_token": "..."
        }
    }

Add a small auth module, likely `src/llm/codex_auth.py` or private helpers in `src/llm/backends/codex.py`, that:

- Reads the auth JSON from a configured path.
- Validates required fields without printing token values.
- Decodes JWT expiry without requiring signature verification.
- Refreshes when the access token is expired or within a configured skew.
- Writes refreshed tokens back atomically with mode `600`.
- Serializes refresh attempts with an async lock to avoid refresh-token reuse races.

When the configured file is `/home/bex/.codex/auth.json`, the backend is intentionally reusing the interactive Codex subscription auth. That is the local service path.

Refresh request:

    POST https://auth.openai.com/oauth/token
    Content-Type: application/x-www-form-urlencoded

    grant_type=refresh_token
    refresh_token=<refresh_token>
    client_id=app_EMoamEEZ73f0CkXaXp7hrann

If refresh fails, raise a sanitized provider/auth error. Do not retry live queue work indefinitely with a known-bad refresh token.

## Plan of Work

### Phase 0: Completed protocol research

This phase is complete. It established that a ChatGPT Codex Responses backend can preserve normal Honcho tool semantics.

Acceptance evidence is recorded in `Progress`, `Surprises & Discoveries`, and `Artifacts and Notes`.

### Phase 1: Config model support

Extend Honcho's configuration types so `transport = "codex"` is valid.

Primary changes:

- `src/config.py::ModelTransport`: include `"codex"`.
- `_normalize_model_transport`: accept `codex/gpt-5.5` shorthand.
- Any default/fallback/override validation that enumerates transports.
- `src/llm/credentials.py`: `codex` should not require `LLM_OPENAI_API_KEY`.

Add small explicit settings. Prefer existing settings style in `src/config.py`.

Recommended fields:

    CODEX_BASE_URL: str = "https://chatgpt.com/backend-api/codex"
    CODEX_AUTH_FILE: str = "/home/bex/.codex/auth.json"
    CODEX_ORIGINATOR: str = "honcho"
    CODEX_USER_AGENT: str = "honcho"
    CODEX_TIMEOUT_SECONDS: float = 600.0
    CODEX_REFRESH_SKEW_SECONDS: int = 300
    CODEX_MAX_CONCURRENT_CALLS: int = 1

Do not change live `config.toml` routing in this phase.

Acceptance:

    cd /home/bex/honcho
    uv run python - <<'PY'
    from src.config import ConfiguredModelSettings
    cfg = ConfiguredModelSettings(transport="codex", model="gpt-5.5")
    print(cfg.transport, cfg.model)
    PY

Expected:

    codex gpt-5.5

### Phase 2: Auth loader and refresh

Implement the auth reader/refresher before live provider calls.

Unit tests should cover:

- Missing auth file.
- Malformed JSON.
- Missing token fields.
- JWT expiry decoding.
- Refresh request body and headers.
- Atomic file update.
- Sanitized error messages.
- Concurrent callers coalescing around one refresh.

Acceptance:

    cd /home/bex/honcho
    uv run pytest tests/llm/test_codex_auth.py -q

### Phase 3: Responses request builder

Implement deterministic conversion helpers, likely in `src/llm/backends/codex.py` or `src/llm/backends/codex_responses.py`.

Helpers:

- `normalize_codex_base_url(base_url) -> str`
- `build_codex_headers(auth, settings) -> dict[str, str]`
- `convert_messages_to_responses_input(messages) -> tuple[str, list[dict]]`
- `convert_tools_to_responses_tools(tools) -> list[dict]`
- `convert_tool_choice(tool_choice, has_tools) -> str | dict | None`
- `build_response_format(response_format) -> dict | None`

Tests should snapshot request bodies for:

- Plain user prompt.
- System plus user messages.
- Assistant tool-call history.
- Tool result history.
- Multiple parallel tool calls.
- `tool_choice` values `auto`, `none`, `required`, `any`, and forced function.
- Tool schemas with optional fields and no strict mode.

Acceptance:

    cd /home/bex/honcho
    uv run pytest tests/llm/test_backends/test_codex.py -q

### Phase 4: CodexResponsesBackend

Add `src/llm/backends/codex.py` with a backend satisfying `ProviderBackend`.

The backend should:

- Use direct `httpx.AsyncClient` SSE calls because the live ChatGPT Codex endpoint requires `stream: true` and custom ChatGPT auth headers.
- Support `complete(...)` with and without tools.
- Support Honcho tool calls through returned `ToolCallResult` values.
- Support `response_format` for structured output when the backend accepts Responses `text.format`; otherwise fail clearly and document the fallback before piloting structured memory paths.
- Do not send `max_output_tokens`, `max_tokens`, or `max_completion_tokens` until a supported Codex cap field is proven; live probes showed all three are rejected by this endpoint.
- Pass `parallel_tool_calls=True` when tools are present.
- Avoid logging raw prompts, raw tool arguments, raw responses, auth tokens, or private memory content.

It is acceptable for `complete(...)` to use a streaming Responses request internally and aggregate events into one `CompletionResult`. This mirrors the Pi path and gives reliable function-call parsing. The public Honcho call still remains non-streaming for tool-loop iterations.

For `stream(...)`, choose one of two explicit behaviors:

1. Implement text streaming with the same Responses event parser, for final no-tools streaming.
2. Raise `ValidationException("codex transport does not support streaming yet")`.

Do not silently buffer and pretend to stream.

Acceptance:

    cd /home/bex/honcho
    uv run pytest tests/llm/test_backends/test_codex.py -q

### Phase 5: Registry, clients, and runtime wiring

Wire the backend into the provider registry.

Update:

- `src/llm/types.py::ProviderClient`
- `src/llm/registry.py::client_for_model_config`
- `src/llm/registry.py::backend_for_provider`
- `src/llm/registry.py::history_adapter_for_provider`
- `src/llm/credentials.py::default_transport_api_key`
- Any `assert_never(provider)` or exhaustive transport match.

Recommended registry shape:

- `codex` does not need an API key from `config.toml`.
- `codex` may use an `AsyncOpenAI` client configured with base URL and placeholder API key, but auth should be resolved per request so refreshed tokens are used.
- Reuse `OpenAIHistoryAdapter` initially. The backend's message converter can convert chat-completions-shaped assistant/tool messages into Responses input items. Avoid broadening the `HistoryAdapter` protocol unless tests force it.

Acceptance is a unit test proving:

    ConfiguredModelSettings(transport="codex", model="gpt-5.5")

resolves to the Codex backend without requiring `LLM_OPENAI_API_KEY`.

### Phase 6: Tool-loop integration tests

Add tests that exercise Honcho's real tool-loop path with a fake Codex backend or fake Responses transport.

Required cases:

- First iteration returns one function call; Honcho executes the tool; second iteration returns final text.
- First iteration uses `tool_choice="required"` or `"any"`; second iteration observes `auto`.
- Multiple parallel tool calls are executed and appended.
- Tool execution error is appended as a tool result and the loop continues.
- Max-iteration behavior still synthesizes a final response or reports the existing warning path.
- Telemetry records tool names and token counts without raw tool arguments.

Acceptance:

    cd /home/bex/honcho
    uv run pytest tests/llm/test_tool_loop.py tests/llm/test_codex_tool_loop.py -q

### Phase 7: Ordinary unit suite

Run focused non-live tests before any real provider call.

Minimum command:

    cd /home/bex/honcho
    HONCHO_CONFIG_TOML_DISABLED=1 uv run pytest \
      tests/llm/test_model_config.py::test_legacy_prefixed_model_strings_are_normalized \
      tests/llm/test_credentials.py \
      tests/llm/test_codex_auth.py \
      tests/llm/test_backends/test_codex.py \
      tests/llm/test_codex_registry.py \
      tests/llm/test_codex_tool_loop.py \
      tests/llm/test_telemetry_llm_call.py::TestEmitLLMCallCompleted::test_codex_transport_emits_token_and_finish_metadata \
      -q

Expected result: all selected tests pass without network calls.

Note: keep TOML loading disabled for this focused suite. The live
`config.toml` intentionally routes dialectic levels to `codex/gpt-5.5`, which
can perturb unrelated defaults-oriented settings tests when the whole
`tests/llm/test_model_config.py` file is run against operational config.

### Phase 8: Live Responses smoke test

Add an explicitly marked live test that proves the actual ChatGPT Codex endpoint works from the repository test harness.

The live test must be skipped by default. It should require a flag such as `--live-llm` and check that the configured `CODEX_AUTH_FILE` exists. The local default should be `/home/bex/.codex/auth.json`.

The live test should call `honcho_llm_call` with:

    model_config=ConfiguredModelSettings(transport="codex", model="gpt-5.5")
    tools=[one harmless local test tool]
    tool_executor=fake or test-only executor
    tool_choice="required"
    max_tool_iterations=3

It should assert:

- The first model turn requests the tool.
- The test executor receives the expected tool name and JSON args.
- The final answer uses the tool result.
- Token counts are non-zero.
- No raw token values appear in captured logs.

Run:

    cd /home/bex/honcho
    uv run pytest tests/live_llm/test_live_codex.py --live-llm -q -n 0

### Phase 9: Deployment wiring

The production backend needs:

- The Honcho Python environment with existing `openai` and `httpx`.
- Read access to ChatGPT Codex auth JSON or an equivalent secret.
- Network access to `chatgpt.com` and `auth.openai.com`.

For Docker Compose, do not bake credentials into the image. The current auth
manager refreshes OAuth tokens atomically, so mount a service-owned auth
directory that is readable and writable by the container user:

    LLM_CODEX_AUTH_FILE=/run/codex-auth/auth.json

Acceptance for Docker wiring:

    cd /home/bex/honcho
    docker compose build api deriver
    docker compose up -d api deriver
    docker exec -i honcho-api-1 python - <<'PY'
    from pathlib import Path
    from src.config import settings
    auth_file = Path(settings.LLM.CODEX_AUTH_FILE)
    print("codex_auth_exists", auth_file.exists())
    print("codex_auth_mode", oct(auth_file.stat().st_mode & 0o777) if auth_file.exists() else "missing")
    PY

Do not continue to live queue tests until the deployment boundary can read auth without exposing token contents.

### Phase 10: Feature-flagged Honcho config pilot

Back up `config.toml` before changing live model settings:

    cd /home/bex/honcho
    cp config.toml config.toml.bak.$(date -u +%Y%m%dT%H%M%SZ)

Add Codex settings but keep existing production model paths unchanged until live smoke tests pass.

Then change exactly one low-risk path to:

    transport = "codex"
    model = "gpt-5.5"

Prefer a bounded dialectic/tool-loop pilot over a high-volume deriver queue pilot, because the immediate requirement is tool-call semantics. Do not move durable dream induction first.

After editing `config.toml`, validate TOML:

    cd /home/bex/honcho
    python - <<'PY'
    import tomllib
    with open("config.toml", "rb") as f:
        tomllib.load(f)
    print("toml-ok")
    PY

Restart only after config parses:

    docker restart honcho-api-1 honcho-deriver-1

Read effective config inside the API container:

    docker exec -i honcho-api-1 python - <<'PY'
    from src.config import settings
    print("deriver", settings.DERIVER.MODEL_CONFIG.transport, settings.DERIVER.MODEL_CONFIG.model)
    print("summary", settings.SUMMARY.MODEL_CONFIG.transport, settings.SUMMARY.MODEL_CONFIG.model)
    print("dream_deduction", settings.DREAM.DEDUCTION_MODEL_CONFIG.transport, settings.DREAM.DEDUCTION_MODEL_CONFIG.model)
    print("dream_induction", settings.DREAM.INDUCTION_MODEL_CONFIG.transport, settings.DREAM.INDUCTION_MODEL_CONFIG.model)
    PY

Acceptance: the effective config shows only the intended dialectic Codex routes; deriver, summary, dream deduction, and dream induction remain on the known-good non-Codex backend until separate structured-output and cost/latency evidence justifies broadening.

### Phase 11: Live queue pilot and monitoring

Before processing live work, inspect queue state:

    docker exec honcho-database-1 psql -U honcho -d honcho -Atc "
    select task_type || ':pending=' || count(*) from queue where processed=false group by task_type order by task_type;
    select task_type || ':errors=' || count(*) from queue where coalesce(error,'') <> '' group by task_type order by task_type;
    select 'active=' || count(*) from active_queue_sessions;
    select work_unit_key || ' age=' || (now() - last_updated) from active_queue_sessions order by last_updated;
    "

Use the watcher for dream/queue health:

    cd /home/bex/honcho
    scripts/watch_dream_health.py --since 15m

For the first live pilot, process one bounded work unit if possible. Watch logs for provider errors, schema validation errors, timeouts, rate-limit errors, auth refresh failures, repeated tool-loop caps, and prompt leaks. Do not paste raw memory content into chat or reports.

Acceptance:

- A real Honcho work item using `transport="codex"` completes.
- At least one real Honcho tool is requested by Codex and executed by Honcho.
- Tool results are fed back to Codex through Responses `function_call_output` semantics.
- The final response is produced after tool execution.
- Queue counts move in the expected direction.
- Token usage is non-zero.
- Logs do not expose auth tokens, raw private memory content, or raw prompt internals.

### Phase 12: Decide what moves to Codex

After the live pilot, decide which model paths should move to Codex.

Candidate paths:

- Dialectic high/max: the most relevant initial candidate if tool-loop quality is the reason for adding Codex.
- Deriver representation generation: only after structured output reliability is proven.
- Summary short and long summaries: good fit if prompt sizes and costs stay reasonable.
- Dream deduction: possible fit if it does not destabilize durable memory.
- Dream induction: only after conservative testing because it affects durable memory and prior model choices avoided censored or unreliable providers.

Acceptance is a written update to this `ROADMAP.md` decision log that records which paths moved, which stayed on Fireworks Kimi or another provider, and why.

### Phase 13: Later Codex-native tools, if needed

Only after the Responses backend is working should Honcho consider Codex-owned tool execution, MCP, or a sidecar bridge.

That later plan must compare at least:

1. Honcho-owned tool loop with Codex as a Responses model backend.
2. Codex-owned dynamic tools bridged back into Honcho.
3. A sidecar Pi/OpenClaw agent bridge.

The recommended current option is still option 1.

## Concrete Steps

1. Confirm the current auth file shape without printing token values.

    Run:

        CODEX_AUTH_FILE="${CODEX_AUTH_FILE:-/home/bex/.codex/auth.json}" uv run python - <<'PY'
        import json, os, pathlib
        p = pathlib.Path(os.environ["CODEX_AUTH_FILE"])
        data = json.loads(p.read_text())
        print("auth_file", p)
        print("auth_mode", data.get("auth_mode"))
        print("token_keys", sorted(data.get("tokens", {}).keys()))
        PY

    Expected keys include `access_token`, `refresh_token`, `account_id`, and `id_token`.

2. Implement Phase 1 config changes and run the config probe.

3. Implement Phase 2 auth loader/refresher and run `tests/llm/test_codex_auth.py`.

4. Implement Phase 3 request conversion helpers and run request-builder tests.

5. Implement `src/llm/backends/codex.py` and run backend tests.

6. Wire registry support and run focused `tests/llm/` tests.

7. Add the live Responses tool-loop smoke and run it only with the live flag.

8. Decide host-side or Docker deployment for the pilot, then run the matching deployment proof.

9. Back up `config.toml`, keep Codex routing bounded to dialectic, restart services, and verify effective config inside the container.

10. Run the live queue pilot and update this plan with results before broadening usage.

## Validation and Acceptance

The implementation is accepted only when all of the following are true:

1. Static config validation accepts `transport="codex"` and rejects unknown transports.
2. The `codex` transport does not require `LLM_OPENAI_API_KEY`.
3. Unit tests pass for auth loading/refresh without live network calls.
4. Unit tests pass for Responses request construction without live network calls.
5. Unit tests pass for Codex backend response normalization without live network calls.
6. Unit tests prove Honcho's existing `execute_tool_loop` works with Codex function calls.
7. A live Responses test passes against the configured `CODEX_AUTH_FILE`, defaulting locally to `/home/bex/.codex/auth.json`, and `gpt-5.5`.
8. A live Responses test proves at least one model-requested tool call is executed by Honcho and fed back as `function_call_output`.
9. Honcho telemetry receives provider input tokens, output tokens, cache-read tokens when available, finish reason, model name, and transport `codex`.
10. The Docker or host deployment boundary can access auth as a secret without baking tokens into an image.
11. A live dialectic pilot completes at least one bounded API call and does not leak tokens, private memory content, or raw prompt internals into logs.
12. This plan's `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` sections are updated with the live pilot result.

## Idempotence and Recovery

When editing `config.toml`, always make a timestamped backup first:

    cp config.toml config.toml.bak.$(date -u +%Y%m%dT%H%M%SZ)

If a Codex-routed live pilot fails, restore the previous model config from the backup, parse TOML, restart `honcho-api-1` and `honcho-deriver-1`, and verify effective settings inside `honcho-api-1`.

If token refresh fails, stop routing live queue work to `codex` until auth is repaired. Do not repeatedly consume a refresh token in a tight retry loop.

If `/home/bex/.codex/auth.json` is missing or invalid, fix the normal Codex login first. Do not copy global state databases unless a specific auth failure proves they are required.

If the ChatGPT Codex endpoint rejects the OpenAI Python client request shape, switch only the transport layer inside `CodexResponsesBackend` to `httpx`; keep the same backend interface, request builder tests, and response normalization tests.

If a queue pilot creates errors, do not bulk requeue until the current config is fixed and no active work claims are using the bad configuration.

## Artifacts and Notes

Default local auth file:

    /home/bex/.codex/auth.json

Docker Compose mounts `/home/bex/.codex` into the API and deriver containers as `/run/codex-auth`.

Local OpenAI Python proof:

    openai 2.36.0
    AsyncOpenAI.responses.create exists
    tool_choice supports none, auto, required, and forced function selection
    tools accepts Responses ToolParam values
    parallel_tool_calls accepts bool

Relevant adjacent evidence in OpenClaw/Pi:

    /home/bex/projects/openclaw/extensions/openai/base-url.ts
    /home/bex/projects/openclaw/extensions/openai/openai-codex-provider.ts
    /home/bex/projects/openclaw/node_modules/@earendil-works/pi-ai/dist/providers/openai-codex-responses.js
    /home/bex/projects/openclaw/node_modules/@earendil-works/pi-ai/dist/providers/openai-responses-shared.js
    /home/bex/projects/openclaw/node_modules/@earendil-works/pi-ai/dist/utils/oauth/openai-codex.js

Important request fields observed in Pi:

    store: false
    stream: true
    text: {"verbosity": "low"}
    include: ["reasoning.encrypted_content"]
    tool_choice: "auto"
    parallel_tool_calls: true
    tools: Responses function tools with strict: null

Important headers observed in Pi:

    Authorization: Bearer <token>
    chatgpt-account-id: <account_id>
    originator: pi
    OpenAI-Beta: responses=experimental
    accept: text/event-stream
    content-type: application/json

## Interfaces and Dependencies

The new backend must satisfy:

    src/llm/backend.py::ProviderBackend.complete(...)
    src/llm/backend.py::ProviderBackend.stream(...)

The new backend must return:

    src/llm/backend.py::CompletionResult

The public response conversion remains owned by:

    src/llm/executor.py::completion_result_to_response

The public entrypoint remains:

    src/llm/api.py::honcho_llm_call

The existing tool-loop owner remains:

    src/llm/tool_loop.py::execute_tool_loop

The config type to extend is:

    src/config.py::ModelTransport

The registry wiring to extend is:

    src/llm/registry.py::client_for_model_config
    src/llm/registry.py::backend_for_provider
    src/llm/registry.py::history_adapter_for_provider
    src/llm/registry.py::get_backend

Production dependencies:

    openai
    httpx
    pydantic

Production must not depend on:

    External Codex SDK packages
    Codex CLI binary
    External Codex agent runtime state
    Codex working directory context

## Revision Notes

- 2026-06-06 / Codex: Implemented `codex` as a ChatGPT Codex Responses backend that preserves Honcho's normal tool-call semantics immediately.
- 2026-06-11 / Codex: Removed the stale isolated-auth Docker boundary; Docker Compose now mounts `/home/bex/.codex` for Codex auth.
