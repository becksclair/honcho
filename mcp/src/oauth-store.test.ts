import { afterEach, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SingleUserOAuthProvider } from "./oauth-provider.js";
import { FileOAuthStateStore } from "./oauth-store.js";

let directory = "";
afterEach(() => { if (directory) rmSync(directory, { recursive: true, force: true }); });

describe("OAuth state", () => {
  test("persists clients and tokens across instances", () => {
    directory = mkdtempSync(join(tmpdir(), "honcho-oauth-"));
    const first = new FileOAuthStateStore(directory);
    first.saveClient({ client_id: "client", client_id_issued_at: 1, redirect_uris: ["https://chatgpt.com/callback"] });
    first.saveToken({ tokenHash: "hash", tokenType: "access", clientId: "client", scopes: ["honcho"], expiresAt: Math.floor(Date.now() / 1000) + 60, resource: new URL("https://honcho.heliasar.com/mcp") });
    const second = new FileOAuthStateStore(directory);
    expect(second.getClient("client")?.client_id).toBe("client");
    expect(second.getToken("hash", "access")?.resource?.href).toBe("https://honcho.heliasar.com/mcp");
  });

  test("retains at most the configured number of clients across instances", () => {
    directory = mkdtempSync(join(tmpdir(), "honcho-oauth-limit-"));
    const first = new FileOAuthStateStore(directory);
    expect(() => new SingleUserOAuthProvider(
      {
        ownerToken: "test-owner-token-long-enough",
        allowedRedirectHosts: ["127.0.0.1"],
        scopes: ["honcho"],
        accessTokenTtlSeconds: 3_600,
        refreshTokenTtlSeconds: 86_400,
        maxRetainedClients: 501,
      },
      new URL("https://honcho.heliasar.com/mcp"),
      first,
    )).toThrow("between 1 and 500");
    expect(first.saveClientIfBelowLimit(
      { client_id: "one", client_id_issued_at: 1, redirect_uris: ["http://127.0.0.1/one"] },
      2,
    )).toBe(true);
    const second = new FileOAuthStateStore(directory);
    expect(second.saveClientIfBelowLimit(
      { client_id: "two", client_id_issued_at: 2, redirect_uris: ["http://127.0.0.1/two"] },
      2,
    )).toBe(true);
    expect(first.saveClientIfBelowLimit(
      { client_id: "three", client_id_issued_at: 3, redirect_uris: ["http://127.0.0.1/three"] },
      2,
    )).toBe(false);
  });
});
