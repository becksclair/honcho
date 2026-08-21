import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { getOAuthProtectedResourceMetadataUrl, mcpAuthRouter } from "@modelcontextprotocol/sdk/server/auth/router.js";
import { requireBearerAuth } from "@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js";
import { InvalidTokenError } from "@modelcontextprotocol/sdk/server/auth/errors.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { checkResourceAllowed, resourceUrlFromServerUrl } from "@modelcontextprotocol/sdk/shared/auth-utils.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";
import type { Request, Response } from "express";
import { createClient, type HonchoConfig } from "./config.js";
import { loadNodeConfig, type NodeConfig } from "./node-config.js";
import { SingleUserOAuthProvider } from "./oauth-provider.js";
import { FileOAuthStateStore } from "./oauth-store.js";
import { createServer as createMcpServer } from "./server.js";

function tryDecodeHonchoJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const json = Buffer.from(parts[1], "base64url").toString("utf8");
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function isHonchoJwtExpired(payload: Record<string, unknown>): boolean {
  const exp = payload.exp;
  if (typeof exp !== "string" || !exp) return false;
  const expTime = new Date(exp);
  if (Number.isNaN(expTime.getTime())) return false;
  return expTime < new Date();
}

function verifyHonchoJwtHs256(token: string, secret: string): boolean {
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  const [headerB64, payloadB64, sigB64] = parts;
  let header: { alg?: string };
  try {
    header = JSON.parse(Buffer.from(headerB64, "base64url").toString("utf8")) as { alg?: string };
  } catch {
    return false;
  }
  if (header.alg !== "HS256") return false;
  const data = `${headerB64}.${payloadB64}`;
  const expected = createHmac("sha256", secret).update(data).digest("base64url");
  const a = Buffer.from(sigB64);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

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

  // Allow `Authorization: Bearer <honcho-jwt>` from `scripts/mint_api_key.py` /
  // `scripts/generate_jwt.py --mcp` to bypass OAuth. The minted token is a
  // Honcho JWT (HS256, exp as ISO string) used as HONCHO_API_KEY. When
  // HONCHO_JWT_SECRET / AUTH_JWT_SECRET is configured we verify the HMAC;
  // otherwise we do a lightweight expiry check and let the Honcho API be the
  // final arbiter (still bypasses the OAuth challenge).
  const combinedVerifier = {
    verifyAccessToken: async (token: string) => {
      try {
        return await provider.verifyAccessToken(token);
      } catch (error) {
        if (!(error instanceof InvalidTokenError)) throw error;
        // Treat bearer as Honcho API key (long-lived JWT) bypass.
        if (!token || token.length < 10) throw error;
        const payload = tryDecodeHonchoJwtPayload(token);
        if (payload) {
          if (isHonchoJwtExpired(payload)) throw new InvalidTokenError("Honcho API key expired");
          // Peer/session scope without workspace is invalid (mirrors src/security.py)
          const peer = payload.p as string | undefined;
          const session = payload.s as string | undefined;
          const workspace = payload.w as string | undefined;
          if ((peer || session) && !workspace) throw new InvalidTokenError("Invalid Honcho scope");
          if (config.honchoJwtSecret) {
            if (!verifyHonchoJwtHs256(token, config.honchoJwtSecret)) throw new InvalidTokenError("Invalid Honcho API key signature");
          }
        } else if (config.honchoJwtSecret) {
          // Not a JWT but a secret is configured — only JWTs are valid bypass tokens.
          throw error;
        }
        // Synthetic OAuth AuthInfo that passes the resource check; the actual
        // Honcho credentials are plumbed per-session from the bearer token.
        return {
          token,
          clientId: "honcho-api-key",
          scopes: ["honcho"],
          resource: resourceUrl,
          expiresAt: Math.floor(Date.now() / 1000) + 3600,
        };
      }
    },
  };

  const bearerAuth = requireBearerAuth({
    verifier: combinedVerifier as unknown as typeof provider,
    requiredScopes: ["honcho"],
    resourceMetadataUrl: getOAuthProtectedResourceMetadataUrl(resourceUrl),
  });
  const transports = new Map<string, Transport>();
  // Keep the Honcho API key that was used to create each MCP session so
  // subsequent `mcp-session-id` requests for the same session reuse the
  // same per-request Honcho identity instead of the static server identity.
  const sessionHonchoConfig = new Map<string, HonchoConfig>();

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
      // `requireBearerAuth` is typed for Node's IncomingMessage but Express
      // Request is compatible at runtime.
      (bearerAuth as unknown as (req: Request, res: Response, next: (error?: unknown) => void) => void)(
        req,
        res,
        (error?: unknown) => {
          res.off("finish", settled);
          res.off("close", settled);
          if (error) reject(error);
          else resolve(false);
        },
      );
    });
    if (denied || res.headersSent) return;
    if (!req.auth?.resource || !checkResourceAllowed({ requestedResource: req.auth.resource, configuredResource: resourceUrl })) {
      jsonRpcError(res, 401, -32001, "Unauthorized");
      return;
    }

    try {
      const sessionId = req.header("mcp-session-id");
      let transport: Transport | undefined;
      // Honcho API-key bypass uses the bearer token as the Honcho API key for
      // this session, not the static server identity. Keep it per-session so
      // `mcp-session-id` follow-ups work without re-sending the workspace.
      const authInfo = (req as unknown as { auth?: { token?: string; clientId?: string } }).auth;
      const isBypass = authInfo?.clientId === "honcho-api-key" && typeof authInfo.token === "string";
      const bypassToken = isBypass ? (authInfo.token as string) : undefined;

      if (sessionId) {
        transport = transports.get(sessionId);
        if (!transport) {
          jsonRpcError(res, 404, -32000, "Unknown MCP session");
          return;
        }
      } else if (req.method === "POST" && isInitializeRequest(req.body)) {
        const honchoConfigForRequest: HonchoConfig = (() => {
          if (!isBypass || !bypassToken) return config.honcho;
          const payload = tryDecodeHonchoJwtPayload(bypassToken);
          const workspaceFromJwt = typeof payload?.w === "string" && payload.w ? (payload.w as string) : undefined;
          const headerWorkspace = req.header("x-honcho-workspace-id")?.trim() || undefined;
          const headerUser = req.header("x-honcho-user-name")?.trim() || undefined;
          const headerAssistant = req.header("x-honcho-assistant-name")?.trim() || undefined;
          return {
            apiKey: bypassToken,
            baseUrl: config.honcho.baseUrl,
            workspaceId: headerWorkspace ?? workspaceFromJwt ?? config.honcho.workspaceId,
            userName: headerUser ?? config.honcho.userName,
            assistantName: headerAssistant ?? config.honcho.assistantName,
          };
        })();
        transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (id) => {
            if (transport) transports.set(id, transport);
            if (isBypass) sessionHonchoConfig.set(id, honchoConfigForRequest);
          },
        });
        transport.onclose = () => {
          if (transport?.sessionId) {
            transports.delete(transport.sessionId);
            sessionHonchoConfig.delete(transport.sessionId);
          }
        };
        // For bypass sessions, `honchoConfigForRequest` already carries the
        // per-request bearer as `apiKey` and the JWT's workspace. For OAuth
        // sessions, use the static server identity.
        const effectiveHonchoConfig = honchoConfigForRequest;
        const effectiveInstructionsConfig: NodeConfig = isBypass
          ? { ...config, honcho: effectiveHonchoConfig }
          : config;
        const server = createMcpServer(
          { honcho: createClient(effectiveHonchoConfig), config: effectiveHonchoConfig },
          instructions(effectiveInstructionsConfig),
        );
        await server.connect(transport);
      } else {
        jsonRpcError(res, 400, -32000, "No valid MCP session");
        return;
      }
      await (transport.handleRequest as unknown as (req: Request, res: Response, body: unknown) => Promise<void>)(req, res, req.body);
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
