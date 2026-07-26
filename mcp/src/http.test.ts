import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Server } from "node:http";
import { createHttpServer } from "./http.js";

const stateDir = mkdtempSync(join(tmpdir(), "honcho-http-"));
let server: Server;
let origin: string;

beforeAll(async () => {
  const { app } = createHttpServer({
    host: "127.0.0.1", port: 7678,
    publicBaseUrl: "https://honcho.heliasar.com",
    stateDir, ownerToken: "test-owner-token-long-enough", trustProxy: true,
    allowedRedirectHosts: ["chatgpt.com", "localhost", "127.0.0.1"],
    honcho: { apiKey: "local-dev", baseUrl: "http://127.0.0.1:8000", workspaceId: "openclaw", userName: "sarah", assistantName: "sarah" },
  });
  server = app.listen(0, "127.0.0.1");
  await new Promise<void>((resolve) => server.once("listening", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No test address");
  origin = `http://127.0.0.1:${address.port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  rmSync(stateDir, { recursive: true, force: true });
});

describe("HTTP MCP", () => {
  test("serves health and OAuth discovery", async () => {
    expect((await fetch(`${origin}/healthz`)).status).toBe(200);
    const metadata = await (await fetch(`${origin}/.well-known/oauth-authorization-server`)).json() as { issuer: string };
    expect(metadata.issuer).toBe("https://honcho.heliasar.com/");
  });

  test("challenges anonymous MCP requests with protected-resource metadata", async () => {
    const response = await fetch(`${origin}/mcp`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "test", version: "1" } } }) });
    expect(response.status).toBe(401);
    expect(response.headers.get("www-authenticate")).toContain("oauth-protected-resource");
  });

  test("rejects the optional standalone SSE stream", async () => {
    const response = await fetch(`${origin}/mcp`);
    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe("POST, DELETE");
  });

  test("rejects dynamic clients with unapproved redirect hosts", async () => {
    const response = await fetch(`${origin}/register`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ client_name: "bad", redirect_uris: ["https://evil.example/callback"] }) });
    expect(response.status).toBeGreaterThanOrEqual(400);
  });
});
