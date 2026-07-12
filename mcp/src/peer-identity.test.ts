import { describe, expect, test } from "bun:test";
import { canonicalPeerId } from "./peer-identity.js";

describe("ChatGPT peer identity mapping", () => {
  test.each([
    ["Sarah", "sarah"],
    ["sarah", "sarah"],
    ["Rebecca_Clair", "bex"],
    ["Bex", "bex"],
    ["bex", "bex"],
    ["  Sarah  ", "sarah"],
    ["codex", "codex"],
  ])("maps %s to %s", (input, expected) => {
    expect(canonicalPeerId(input)).toBe(expected);
  });
});
