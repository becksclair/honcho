const CANONICAL_PEER_ID = /^[a-z][a-z0-9_-]{0,63}$/;

const DEFAULT_PEER_ALIASES: Readonly<Record<string, string>> = {
  Sarah: "sarah",
  sarah: "sarah",
  Rebecca_Clair: "bex",
  Bex: "bex",
  bex: "bex",
  user: "bex",
  Sky: "sky",
  sky: "sky",
  "agent-sky": "sky",
  "pi-sky": "sky",
  Luke: "luke",
  luke: "luke",
  "agent-luke": "luke",
  Kate: "kate",
  kate: "kate",
  codex: "kate",
  "agent-codex": "kate",
};

function aliasKey(peerId: string): string {
  return peerId.normalize("NFKC").trim().toLowerCase();
}

function buildAliasMap(source: Readonly<Record<string, string>>): Map<string, string> {
  const aliases = new Map<string, string>();
  for (const [presentedId, canonicalId] of Object.entries(source)) {
    const key = aliasKey(presentedId);
    const canonical = canonicalId.normalize("NFKC").trim();
    if (!key) throw new Error("Peer alias keys cannot be blank");
    if (!CANONICAL_PEER_ID.test(canonical)) {
      throw new Error(`Invalid canonical peer ID: ${canonicalId}`);
    }
    const existing = aliases.get(key);
    if (existing && existing !== canonical) {
      throw new Error(`Peer aliases collide after normalization: ${presentedId}`);
    }
    aliases.set(key, canonical);
  }
  return aliases;
}

export function parsePeerAliases(raw: string | undefined): Map<string, string> {
  if (!raw?.trim()) return buildAliasMap(DEFAULT_PEER_ALIASES);

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error("HONCHO_PEER_ALIASES_JSON must be valid JSON", { cause: error });
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("HONCHO_PEER_ALIASES_JSON must be a JSON object");
  }
  for (const value of Object.values(parsed)) {
    if (typeof value !== "string") {
      throw new Error("HONCHO_PEER_ALIASES_JSON values must be strings");
    }
  }
  return buildAliasMap(parsed as Record<string, string>);
}

let cachedRaw: string | undefined;
let cachedAliases: Map<string, string> | undefined;

function activeAliases(): Map<string, string> {
  const raw = process.env.HONCHO_PEER_ALIASES_JSON;
  if (!cachedAliases || raw !== cachedRaw) {
    cachedRaw = raw;
    cachedAliases = parsePeerAliases(raw);
  }
  return cachedAliases;
}

/** Normalize peer IDs before they reach Honcho. Honcho remains authoritative. */
export function canonicalPeerId(peerId: string): string {
  const trimmed = peerId.normalize("NFKC").trim();
  return activeAliases().get(aliasKey(trimmed)) ?? trimmed;
}
