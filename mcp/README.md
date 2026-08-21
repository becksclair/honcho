# Honcho MCP Server

A dual-runtime Model Context Protocol server for Honcho. It can run as the
original Cloudflare Worker or as a persistent Node Streamable HTTP server with
OAuth for ChatGPT.

## ChatGPT deployment

The local production server is available at:

```
https://honcho.heliasar.com/mcp
```

The Node runtime terminates MCP OAuth itself and uses server-side Honcho
credentials. ChatGPT does not need, and cannot override, the fixed deployment
identity (`openclaw` workspace, `bex` user, `sarah` assistant). ChatGPT aliases
are normalized at peer creation, session membership, and message ingestion:
`Sarah` becomes `sarah`, while `Rebecca_Clair` and `Bex` become `bex`.

Build and run it locally with:

```bash
bun run build:node
HONCHO_MCP_OAUTH_OWNER_TOKEN='<at-least-16-characters>' \
  HONCHO_MCP_PUBLIC_BASE_URL='https://honcho.heliasar.com' \
  bun run start:node
```

The production user service reads `/home/bex/.config/honcho-mcp/env`. OAuth
clients and hashed access/refresh tokens persist under
`~/.local/share/honcho-mcp/`.

## Quickstart: Use the Hosted Server

1. Get an API key at <https://app.honcho.dev>
2. Add Honcho to your Claude Desktop config:

```json
{
  "mcpServers": {
    "honcho": {
      "command": "bunx",
      "args": [
        "mcp-remote",
        "https://mcp.honcho.dev",
        "--header",
        "Authorization:${AUTH_HEADER}",
        "--header",
        "X-Honcho-User-Name:${USER_NAME}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer <your-honcho-key>",
        "USER_NAME": "<your-name>"
      }
    }
  }
}
```

### Optional Headers

| Header | Default | Description |
| --- | --- | --- |
| `X-Honcho-Workspace-ID` | `"default"` | Workspace to operate in |

## Available Tools

**Workspace:** `inspect_workspace` (aggregates metadata, configuration, and peer/session IDs), `list_workspaces` (enumerates accessible workspaces), `search` (semantic search scoped by optional peer/session params), `get_metadata`, `set_metadata`

**Peers:** `create_peer`, `list_peers`, `chat`, `get_peer_card`, `set_peer_card`, `get_peer_context`, `get_representation`

**Sessions:** `create_session`, `list_sessions`, `delete_session`, `clone_session`, `add_peers_to_session`, `remove_peers_from_session`, `get_session_peers`, `inspect_session`, `add_messages_to_session`, `get_session_messages`, `get_session_message`, `get_session_context`

**Conclusions:** `list_conclusions`, `query_conclusions`, `create_conclusions`, `delete_conclusion`

**System:** `schedule_dream`, `get_queue_status`

## Architecture

```
src/
  index.ts              # Worker entry point — parse config, delegate to MCP handler
  server.ts             # createServer() — registers all tools on an McpServer
  config.ts             # HonchoConfig, parseConfig(), createClient()
  peer-identity.ts      # ChatGPT peer alias normalization
  types.ts              # ToolContext, result helpers
  tools/
    workspace.ts        # inspect, list, search, metadata
    peers.ts            # CRUD, chat, card, context, representation
    sessions.ts         # CRUD, peers, messages, inspect, context, clone
    conclusions.ts      # list, query, create, delete
    system.ts           # dream, queue status
```

Built on:

- **[agents](https://www.npmjs.com/package/agents)** — `createMcpHandler` for Cloudflare Workers
- **[@modelcontextprotocol/sdk](https://www.npmjs.com/package/@modelcontextprotocol/sdk)** — `McpServer` for tool registration
- **[@honcho-ai/sdk](https://www.npmjs.com/package/@honcho-ai/sdk)** v2 — Honcho TypeScript SDK

## Self-Hosted Honcho (stdio, no OAuth)

For Claude Code / OpenCode / local MCP clients you don't need OAuth. Mint a
long-lived `HONCHO_API_KEY` outside the honcho containers and run
`honcho-mcp-stdio`:

```bash
# 1. Mint a key (runs outside docker, signs with AUTH_JWT_SECRET from env/.env/config.toml)
uv run python scripts/mint_api_key.py --workspace openclaw --peer codex --expires 1y --format env
# or: uv run python scripts/generate_jwt.py --mcp --workspace openclaw --peer codex --expires 1y --format env

# 2. Configure MCP (Claude Code example)
# The script prints: HONCHO_API_KEY=<jwt>
export HONCHO_API_KEY=<paste-jwt>
export HONCHO_API_URL=http://127.0.0.1:8000
export HONCHO_WORKSPACE_ID=openclaw
honcho-mcp-stdio   # or point your client at mcp/honcho-mcp-stdio

# Other scopes:
uv run python scripts/mint_api_key.py --workspace my-app --expires 90d --format json  # inspect
uv run python scripts/generate_jwt.py --admin --expires 24h --format env               # admin key
```

Notes:
- `AUTH_JWT_SECRET` must match the value mounted into the `honcho-api`/`honcho-deriver`
  containers (bind-mounted `config.toml` on Saga via `/opt/homelab/compose.yaml`).
  Generate with `uv run python scripts/generate_jwt_secret.py --print-only` if unset.
- Stateless JWTs cannot be revoked without rotating `AUTH_JWT_SECRET`; prefer the
  shortest workable `--expires`.
- For the hosted Worker (HTTP/OAuth) path, `HONCHO_API_URL` routing is below.

## Self-Hosted Honcho (Worker / HTTP)

**Worker (`mcp/src/index.ts`)** already accepts `Authorization: Bearer <honcho-jwt>` per-request via `X-Honcho-*` headers — no OAuth needed.

**Node HTTP (`mcp/src/http.ts` `/mcp`)** now does too: `Authorization: Bearer` is tried as OAuth first, then as a Honcho API key (long-lived JWT from `scripts/mint_api_key.py` / `scripts/generate_jwt.py --mcp`) to bypass the OAuth challenge entirely. The bearer becomes the per-session `HONCHO_API_KEY` (workspace from JWT `w` or `X-Honcho-Workspace-ID` header). Peer/session scope without workspace is rejected (mirrors `src/security.py`).

Set `HONCHO_JWT_SECRET` (or `AUTH_JWT_SECRET`) on the Node MCP server to the same value as `honcho-api`’s `AUTH_JWT_SECRET` for strict HMAC verification:

```bash
# honcho-mcp env (Saga: /home/bex/.config/honcho-mcp/env)
HONCHO_JWT_SECRET=<same-as-AUTH_JWT_SECRET>
# or AUTH_JWT_SECRET=<same-as-AUTH_JWT_SECRET>
```

Without it, `/mcp` still accepts the bearer but only checks JWT format, `exp` (ISO string) expiry, and `w`/`p`/`s` scope — the Honcho API remains the final arbiter (forged keys get a 200 MCP session but Honcho calls 401). For production, set the secret.

```
HONCHO_API_URL=http://127.0.0.1:28000
```

**Deployed Worker:**

```bash
wrangler secret put HONCHO_API_URL
# paste your URL when prompted
```

When `HONCHO_API_URL` is unset the Worker routes to `https://api.honcho.dev`,
so this change is backward-compatible.

## Development

### Setup

```bash
bun install
```

### Local dev

```bash
bun dev
```

### Type-check

```bash
bun run tsc --noEmit
```

### Test locally

```bash
bunx mcp-remote http://localhost:8787 \
  --header "Authorization:Bearer <key>" \
  --header "X-Honcho-User-Name:test"
```

### Deploy

```bash
bun run deploy              # production
bun run deploy:staging      # staging
```
