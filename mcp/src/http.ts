import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { getOAuthProtectedResourceMetadataUrl, mcpAuthRouter } from "@modelcontextprotocol/sdk/server/auth/router.js";
import { requireBearerAuth } from "@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { checkResourceAllowed, resourceUrlFromServerUrl } from "@modelcontextprotocol/sdk/shared/auth-utils.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";
import type { Request, Response } from "express";
import { createClient } from "./config.js";
import { loadNodeConfig, type NodeConfig } from "./node-config.js";
import { SingleUserOAuthProvider } from "./oauth-provider.js";
import { FileOAuthStateStore } from "./oauth-store.js";
import { createServer as createMcpServer } from "./server.js";

type Transport = StreamableHTTPServerTransport;

function jsonRpcError(res: Response, status: number, code: number, message: string): void {
  res.status(status).json({ jsonrpc: "2.0", error: { code, message }, id: null });
}

function instructions(config: NodeConfig): string {
  const base = readFileSync(fileURLToPath(new URL("../instructions.md", import.meta.url)), "utf8");
  return `${base}\n\n## ChatGPT identities\n\nThe human user is \`${config.honcho.userName}\`. The ChatGPT assistant is \`${config.honcho.assistantName}\`. Always use these lowercase peer IDs when creating peers, adding session participants, or recording messages.`;
}

export function createHttpServer(config: NodeConfig = loadNodeConfig()) {
  const app = createMcpExpressApp({
    host: config.host,
    allowedHosts: ["localhost", "127.0.0.1", new URL(config.publicBaseUrl).hostname],
  });
  if (config.trustProxy) app.set("trust proxy", 1);

  app.use((_req, res, next) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    next();
  });

  app.use((req, res, next) => {
    const startedAt = performance.now();
    res.on("finish", () => {
      console.log(JSON.stringify({
        event: "http_request",
        method: req.method,
        path: req.path,
        status: res.statusCode,
        durationMs: Math.round(performance.now() - startedAt),
        clientIp: req.header("cf-connecting-ip") ?? req.ip,
      }));
    });
    next();
  });

  const mcpUrl = new URL("/mcp", config.publicBaseUrl);
  const resourceUrl = resourceUrlFromServerUrl(mcpUrl);
  const provider = new SingleUserOAuthProvider({
    ownerToken: config.ownerToken,
    allowedRedirectHosts: config.allowedRedirectHosts,
    scopes: ["honcho"],
    accessTokenTtlSeconds: 3600,
    refreshTokenTtlSeconds: 30 * 24 * 3600,
  }, mcpUrl, new FileOAuthStateStore(config.stateDir));

  app.use(mcpAuthRouter({
    provider,
    issuerUrl: new URL(config.publicBaseUrl),
    baseUrl: new URL(config.publicBaseUrl),
    resourceServerUrl: resourceUrl,
    scopesSupported: ["honcho"],
    resourceName: "Honcho",
  }));

  const bearerAuth = requireBearerAuth({
    verifier: provider,
    requiredScopes: ["honcho"],
    resourceMetadataUrl: getOAuthProtectedResourceMetadataUrl(resourceUrl),
  });
  const transports = new Map<string, Transport>();

  app.get("/healthz", (_req, res) => res.json({ ok: true, name: "honcho-mcp" }));
  app.get("/mcp", (_req, res) => {
    res.setHeader("Allow", "POST, DELETE");
    res.sendStatus(405);
  });

  app.all("/mcp", async (req: Request, res: Response) => {
    const denied = await new Promise<boolean>((resolve, reject) => {
      const settled = () => resolve(true);
      res.once("finish", settled);
      res.once("close", settled);
      bearerAuth(req, res, (error?: unknown) => {
        res.off("finish", settled);
        res.off("close", settled);
        if (error) reject(error); else resolve(false);
      });
    });
    if (denied || res.headersSent) return;
    if (!req.auth?.resource || !checkResourceAllowed({ requestedResource: req.auth.resource, configuredResource: resourceUrl })) {
      jsonRpcError(res, 401, -32001, "Unauthorized");
      return;
    }

    try {
      const sessionId = req.header("mcp-session-id");
      let transport: Transport | undefined;
      if (sessionId) {
        transport = transports.get(sessionId);
        if (!transport) {
          jsonRpcError(res, 404, -32000, "Unknown MCP session");
          return;
        }
      } else if (req.method === "POST" && isInitializeRequest(req.body)) {
        transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (id) => { if (transport) transports.set(id, transport); },
        });
        transport.onclose = () => {
          if (transport?.sessionId) transports.delete(transport.sessionId);
        };
        const server = createMcpServer({ honcho: createClient(config.honcho), config: config.honcho }, instructions(config));
        await server.connect(transport);
      } else {
        jsonRpcError(res, 400, -32000, "No valid MCP session");
        return;
      }
      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      console.error("MCP request failed", error);
      if (!res.headersSent) jsonRpcError(res, 500, -32603, "Internal server error");
    }
  });

  return { app, config };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const { app, config } = createHttpServer();
  app.listen(config.port, config.host, () => {
    console.log(`honcho-mcp listening on http://${config.host}:${config.port}/mcp`);
    console.log(`public URL: ${config.publicBaseUrl}/mcp`);
    console.log(`identity: ${config.honcho.workspaceId}/${config.honcho.userName}`);
    console.log(`trust proxy: ${config.trustProxy ? "enabled" : "disabled"}`);
  });
}
