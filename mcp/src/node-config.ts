import { homedir } from "node:os";
import { join, resolve } from "node:path";
import type { HonchoConfig } from "./config.js";

export interface NodeConfig {
  host: string;
  port: number;
  publicBaseUrl: string;
  stateDir: string;
  ownerToken: string;
  trustProxy: boolean;
  allowedRedirectHosts: string[];
  honcho: HonchoConfig;
  honchoJwtSecret?: string;
}

function value(env: NodeJS.ProcessEnv, name: string): string | undefined {
  const result = env[name]?.trim();
  return result || undefined;
}

function required(env: NodeJS.ProcessEnv, name: string): string {
  const result = value(env, name);
  if (!result) throw new Error(`${name} is required`);
  return result;
}

function parsePort(raw: string | undefined): number {
  const port = Number(raw ?? "7678");
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid HONCHO_MCP_PORT: ${raw}`);
  }
  return port;
}

export function loadNodeConfig(env: NodeJS.ProcessEnv = process.env): NodeConfig {
  const host = value(env, "HONCHO_MCP_HOST") ?? "127.0.0.1";
  const port = parsePort(value(env, "HONCHO_MCP_PORT"));
  const publicBaseUrl = (value(env, "HONCHO_MCP_PUBLIC_BASE_URL") ?? `http://${host}:${port}`).replace(/\/+$/, "");
  const parsedPublicUrl = new URL(publicBaseUrl);
  if (parsedPublicUrl.protocol !== "http:" && parsedPublicUrl.protocol !== "https:") {
    throw new Error("HONCHO_MCP_PUBLIC_BASE_URL must be HTTP(S)");
  }

  const ownerToken = required(env, "HONCHO_MCP_OAUTH_OWNER_TOKEN");
  if (ownerToken.length < 16) throw new Error("HONCHO_MCP_OAUTH_OWNER_TOKEN must be at least 16 characters");

  return {
    host,
    port,
    publicBaseUrl,
    stateDir: resolve(value(env, "HONCHO_STATE_DIR") ?? join(homedir(), ".local", "share", "honcho-mcp")),
    ownerToken,
    trustProxy: ["1", "true", "yes", "on"].includes((value(env, "HONCHO_MCP_TRUST_PROXY") ?? "").toLowerCase()),
    allowedRedirectHosts: (value(env, "HONCHO_MCP_OAUTH_ALLOWED_REDIRECT_HOSTS") ?? "chatgpt.com,localhost,127.0.0.1")
      .split(",").map((entry) => entry.trim()).filter(Boolean),
    honcho: {
      apiKey: value(env, "HONCHO_API_KEY") ?? "local-dev",
      baseUrl: value(env, "HONCHO_API_URL") ?? "http://127.0.0.1:8000",
      workspaceId: value(env, "HONCHO_WORKSPACE_ID") ?? "openclaw",
      userName: value(env, "HONCHO_USER_NAME") ?? "bex",
      assistantName: value(env, "HONCHO_ASSISTANT_NAME") ?? "sarah",
    },
    honchoJwtSecret: value(env, "HONCHO_JWT_SECRET") ?? value(env, "AUTH_JWT_SECRET"),
  };
}
