const CHATGPT_PEER_ALIASES = new Map<string, string>([
  ["Sarah", "sarah"],
  ["sarah", "sarah"],
  ["Rebecca_Clair", "bex"],
  ["Bex", "bex"],
  ["bex", "bex"],
]);

/** Normalize peer IDs emitted by ChatGPT before they reach Honcho. */
export function canonicalPeerId(peerId: string): string {
  const trimmed = peerId.trim();
  return CHATGPT_PEER_ALIASES.get(trimmed) ?? trimmed;
}
