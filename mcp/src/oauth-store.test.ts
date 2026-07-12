import { afterEach, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
});
