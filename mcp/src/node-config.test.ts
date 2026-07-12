import { describe, expect, test } from "bun:test";
import { loadNodeConfig } from "./node-config.js";

const base = {
  HONCHO_MCP_OAUTH_OWNER_TOKEN: "test-owner-token-long-enough",
  HOME: "/tmp",
};

describe("node config", () => {
  test("uses the production Honcho identity defaults", () => {
    const config = loadNodeConfig(base);
    expect(config.port).toBe(7678);
    expect(config.honcho).toEqual({
      apiKey: "local-dev",
      baseUrl: "http://127.0.0.1:8000",
      workspaceId: "openclaw",
      userName: "bex",
      assistantName: "sarah",
    });
  });

  test("requires a strong owner token", () => {
    expect(() => loadNodeConfig({ ...base, HONCHO_MCP_OAUTH_OWNER_TOKEN: "short" })).toThrow();
  });
});
