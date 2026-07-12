# Honcho Local Operations

This checkout is the live self-hosted Honcho service used by OpenClaw. Treat
`config.toml`, Docker containers, and the Postgres queue as operational state,
not just source code.

## Orientation

Important local files and services:

- `config.toml` - live Honcho config for LLMs, deriver, dreams, embeddings, DB,
  and queue behavior.
- `docker-compose.yml` - local Honcho stack.
- `honcho-api-1` - API container.
- `honcho-deriver-1` - background deriver/dream/reconciler worker.
- `honcho-database-1` - Postgres queue, documents, embeddings.
- `honcho-redis-1` - cache/coordination.
- `scripts/watch_dream_health.py` - local health watcher for dream/replay issues.

Back up `config.toml` before changing it:

```bash
cp config.toml config.toml.bak.$(date -u +%Y%m%dT%H%M%SZ)
```

## Validation

After config edits:

```bash
python - <<'PY'
import tomllib
with open("config.toml", "rb") as f:
    tomllib.load(f)
print("toml-ok")
PY
docker restart honcho-api-1 honcho-deriver-1
docker exec -i honcho-api-1 python - <<'PY'
from src.config import settings
print("workers", settings.DERIVER.WORKERS)
print("stale", settings.DERIVER.STALE_SESSION_TIMEOUT_MINUTES)
print("dream_threshold", settings.DREAM.DOCUMENT_THRESHOLD)
print("dream_min_hours", settings.DREAM.MIN_HOURS_BETWEEN_DREAMS)
print("dream_deduction", settings.DREAM.DEDUCTION_MODEL_CONFIG.transport, settings.DREAM.DEDUCTION_MODEL_CONFIG.model)
print("dream_induction", settings.DREAM.INDUCTION_MODEL_CONFIG.transport, settings.DREAM.INDUCTION_MODEL_CONFIG.model)
PY
```

Check live OpenClaw integration separately:

```bash
openclaw honcho status
```

## Current Model Lessons

Current live model backbone:

- Deriver: `codex/gpt-5.6-luna` with `thinking_effort = "xhigh"` on the normal tier.
- Summary: `codex/gpt-5.6-luna` with `thinking_effort = "xhigh"` on the normal tier.
- Dream deduction/induction: `codex/gpt-5.6-luna` with
  `thinking_effort = "xhigh"` on the normal tier by default.
- Dialectic synthesis: `minimal` through `high` use `codex/gpt-5.6-luna` with
  `thinking_effort = "xhigh"`; `max` uses the model's highest wire effort,
  `thinking_effort = "max"`. Codex CLI's `ultra` mode also sends `max` on the
  wire and adds client-side proactive subagents, which Honcho does not emulate.
  `minimal` and `low` stay normal tier; `medium`, `high`, and `max` use
  `service_tier = "priority"`.
- GPT-5.6 models use the Responses-Lite contract and require the supported
  Codex client identity: `CODEX_ORIGINATOR = "codex_exec"` and
  `CODEX_USER_AGENT = "codex_exec/0.144.1"`. With either field left as
  `honcho`, the ChatGPT subscription endpoint returns `Model not found` for
  `gpt-5.6-luna` even when the account and Lite request shape are otherwise valid.
- Local embeddings: `bge-m3:latest` through Ollama-compatible OpenAI API; verify
  the current `base_url` from the running config because it may move between
  Docker-host and Tailscale addresses.

DeepSeek V4 Pro:

- Works for plain/tool-loop calls through OpenCode Go OpenAI-compatible route:
  - `transport = "openai"`
  - `model = "deepseek-v4-pro"`
  - `base_url = "https://opencode.ai/zen/go/v1"`
  - `api_key_env = "OPENCODE_GO_API_KEY"`
- Does not support OpenAI `json_schema` structured output through this route.
  Do not use it for `deriver`, which uses `response_model=PromptRepresentation`.
- For dialectic high/max, use `TOOL_CHOICE = "auto"`. DeepSeek fails with
  forced `any`/`required` tool choice.
- Tool loops may hit max iterations and still complete successfully. Treat that
  as a warning to inspect, not automatically as failure.

Qwen / Alibaba:

- Avoid Qwen for durable Honcho memory work. It can hit Alibaba content-filter
  behavior, and we do not want censored-model outputs in dream induction.
- `qwen3.7-max` passed simple Honcho-shaped probes only via Anthropic-style
  OpenCode Go route:
  - `transport = "anthropic"`
  - `base_url = "https://opencode.ai/zen/go"`
- It was removed from `dream.induction_model_config`; requeue any dream jobs
  that ran while Qwen was configured.

MiniMax:

- A raw Anthropic-style ping worked, but Honcho structured/tool-wrapper behavior
  was not trusted. Do not use it for production memory paths without retesting.

## Temporary Replay Settings

During the May 31, 2026 reprocessing/backfill, these temporary values were
applied:

```toml
[deriver]
WORKERS = 2
STALE_SESSION_TIMEOUT_MINUTES = 15

[dream]
DOCUMENT_THRESHOLD = 100
MIN_HOURS_BETWEEN_DREAMS = 12
```

Do not raise `WORKERS` casually. The local machine has headroom, but extra
workers can run more expensive dream jobs in parallel, increase duplicate
detection churn, and pressure OpenCode Go / Fireworks.

When reprocessing is complete, consider restoring the normal dream cadence:

```toml
[deriver]
STALE_SESSION_TIMEOUT_MINUTES = 5

[dream]
DOCUMENT_THRESHOLD = 35
MIN_HOURS_BETWEEN_DREAMS = 6
```

Only keep the slower settings if live evidence says the new cadence is better.

During deliberate large memory rebuilds, replays, or backfills where completion
time matters, temporarily put the dream synthesis paths on Codex priority tier:

```toml
[dream.deduction_model_config.overrides.provider_params]
service_tier = "priority"

[dream.induction_model_config.overrides.provider_params]
service_tier = "priority"
```

Prefer leaving `deriver` and `summary` on the normal tier unless live evidence
says their latency is directly blocking the rebuild. When the rebuild is done
and the queue is drained, remove the temporary dream priority-tier blocks,
restart `honcho-api-1` and `honcho-deriver-1`, and verify the running config has
`dream.deduction`, `dream.induction`, `deriver`, and `summary` provider params
back to `{}`.

## Queue Operations

Queue status:

```bash
docker exec honcho-database-1 psql -U honcho -d honcho -Atc "
select task_type || ':pending=' || count(*) from queue where processed=false group by task_type order by task_type;
select task_type || ':processed=' || count(*) from queue where processed=true group by task_type order by task_type;
select task_type || ':errors=' || count(*) from queue where coalesce(error,'') <> '' group by task_type order by task_type;
select 'active=' || count(*) from active_queue_sessions;
select work_unit_key || ' age=' || (now() - last_updated) from active_queue_sessions order by last_updated;
"
```

Requeue old provider/config failures only after confirming the current config is
fixed. Examples used during this replay:

```sql
update queue set processed=false, error=null
where task_type='representation'
  and processed=true
  and error like 'RetryError:%AuthenticationError%';

update queue set processed=false, error=null
where task_type='representation'
  and processed=true
  and error like '%BadRequestError%';
```

For Qwen exposure in dreams, we conservatively requeued processed dream rows
after removing Qwen:

```sql
update queue set processed=false, error=null
where task_type='dream'
  and processed=true;
```

Only do this when no dream claims are active:

```sql
select count(*) from active_queue_sessions where work_unit_key like 'dream:%';
```

## Health Watching

Use the watcher instead of manually scanning large logs:

```bash
scripts/watch_dream_health.py --since 15m
```

The watcher reports:

- queue pending/processed/error counts
- active work unit ages
- dream starts/completions
- tool-loop cap warnings
- hard model/request failures
- Qwen/Alibaba mentions
- sanitized snippets only

Exit codes:

- `0` - ok
- `1` - warning, such as tool-loop cap or Qwen/Alibaba mention
- `2` - hard problem, such as BadRequest, RetryError, auth/model error, traceback

## Standalone Codex MCP Wiring

Standalone Codex CLI is wired to Honcho through a local stdio MCP wrapper:

```toml
[mcp_servers.honcho]
command = "/home/bex/honcho/mcp/honcho-mcp-stdio"
enabled = true
```

The wrapper lives at `mcp/honcho-mcp-stdio` and runs `mcp/src/stdio.ts`.
Defaults:

- `HONCHO_API_URL=http://127.0.0.1:8000`
- `HONCHO_WORKSPACE_ID=openclaw`
- `HONCHO_USER_NAME=codex`
- `HONCHO_ASSISTANT_NAME=codex`
- `HONCHO_API_KEY=local-dev`

The `codex` peer has been created in the `openclaw` workspace with
`observeMe=true`. The MCP server instructions tell clients to use `codex` as the
default standalone Codex peer when a tool requires `peer_id` and the user has not
requested a different peer.

Smoke-test the MCP wrapper without starting Codex:

```bash
bun - <<'TS'
import { Client } from '/home/bex/honcho/mcp/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '/home/bex/honcho/mcp/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
const transport = new StdioClientTransport({ command: '/home/bex/honcho/mcp/honcho-mcp-stdio' });
const client = new Client({ name: 'honcho-mcp-smoke', version: '0.0.1' });
await client.connect(transport);
console.log(client.getInstructions());
console.log((await client.listTools()).tools.map((t) => t.name).join(','));
await client.close();
TS
```

## Log Privacy

Deriver/dream logs can print sensitive memory content, especially surprisal
selection, duplicate detection, and dream summaries. Do not paste raw log lines
into chat or reports. Redact names, IDs, transcripts, locations, exact private
facts, tokens, and file paths unless the user explicitly asks for raw evidence
and it is safe to show.
