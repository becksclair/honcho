import { afterEach, describe, expect, test } from "bun:test";
import { canonicalPeerId, parsePeerAliases } from "./peer-identity.js";

const originalAliases = process.env.HONCHO_PEER_ALIASES_JSON;

afterEach(() => {
  if (originalAliases === undefined) delete process.env.HONCHO_PEER_ALIASES_JSON;
  else process.env.HONCHO_PEER_ALIASES_JSON = originalAliases;
});

describe("peer identity mapping", () => {
  test.each([
    ["Sarah", "sarah"],
    ["sarah", "sarah"],
    ["Rebecca_Clair", "bex"],
    ["BEX", "bex"],
    ["user", "bex"],
    ["Sky", "sky"],
    ["agent-sky", "sky"],
    ["Luke", "luke"],
    ["Codex", "kate"],
    ["agent-codex", "kate"],
    ["  Kate  ", "kate"],
  ])("maps %s to %s", (input, expected) => {
    expect(canonicalPeerId(input)).toBe(expected);
  });

  test("uses a deployment-provided alias manifest", () => {
    process.env.HONCHO_PEER_ALIASES_JSON = JSON.stringify({
      Rebecca: "bex",
      SarahGPT: "sarah",
    });

    expect(canonicalPeerId("Rebecca")).toBe("bex");
    expect(canonicalPeerId("SarahGPT")).toBe("sarah");
    expect(canonicalPeerId("codex")).toBe("codex");
  });

  test("rejects invalid manifests", () => {
    expect(() => parsePeerAliases("[]")).toThrow("JSON object");
    expect(() => parsePeerAliases('{"Codex":"Kate"}')).toThrow(
      "Invalid canonical peer ID",
    );
    expect(() => parsePeerAliases("not-json")).toThrow("valid JSON");
  });
});
