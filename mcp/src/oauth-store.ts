import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  OAuthClientInformationFullSchema,
  type OAuthClientInformationFull,
} from "@modelcontextprotocol/sdk/shared/auth.js";

export type OAuthTokenType = "access" | "refresh";

export interface StoredOAuthToken {
  tokenHash: string;
  tokenType: OAuthTokenType;
  clientId: string;
  scopes: string[];
  expiresAt: number;
  resource?: URL;
}

interface StateFile {
  clients: Record<string, OAuthClientInformationFull>;
  tokens: Record<string, Omit<StoredOAuthToken, "resource"> & { resource?: string }>;
}

export interface OAuthStateStore {
  getClient(clientId: string): OAuthClientInformationFull | undefined;
  saveClient(client: OAuthClientInformationFull): void;
  getToken(hash: string, type: OAuthTokenType): StoredOAuthToken | undefined;
  saveToken(token: StoredOAuthToken): void;
  deleteToken(hash: string, type?: OAuthTokenType): void;
}

export class FileOAuthStateStore implements OAuthStateStore {
  private readonly path: string;
  private state: StateFile;

  constructor(stateDir: string) {
    mkdirSync(stateDir, { recursive: true, mode: 0o700 });
    this.path = join(stateDir, "oauth-state.json");
    this.state = this.read();
    this.purge();
  }

  getClient(clientId: string): OAuthClientInformationFull | undefined {
    const client = this.state.clients[clientId];
    return client ? OAuthClientInformationFullSchema.parse(client) : undefined;
  }

  saveClient(client: OAuthClientInformationFull): void {
    this.state.clients[client.client_id] = client;
    this.flush();
  }

  getToken(hash: string, type: OAuthTokenType): StoredOAuthToken | undefined {
    this.purge();
    const token = this.state.tokens[hash];
    if (!token || token.tokenType !== type) return undefined;
    return { ...token, resource: token.resource ? new URL(token.resource) : undefined };
  }

  saveToken(token: StoredOAuthToken): void {
    this.state.tokens[token.tokenHash] = {
      ...token,
      resource: token.resource?.href,
    };
    this.flush();
  }

  deleteToken(hash: string, type?: OAuthTokenType): void {
    const token = this.state.tokens[hash];
    if (token && (!type || token.tokenType === type)) {
      delete this.state.tokens[hash];
      this.flush();
    }
  }

  private read(): StateFile {
    try {
      const parsed = JSON.parse(readFileSync(this.path, "utf8")) as StateFile;
      return { clients: parsed.clients ?? {}, tokens: parsed.tokens ?? {} };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      return { clients: {}, tokens: {} };
    }
  }

  private purge(): void {
    const now = Math.floor(Date.now() / 1000);
    let changed = false;
    for (const [hash, token] of Object.entries(this.state.tokens)) {
      if (token.expiresAt < now) {
        delete this.state.tokens[hash];
        changed = true;
      }
    }
    if (changed) this.flush();
  }

  private flush(): void {
    const temporary = `${this.path}.tmp`;
    writeFileSync(temporary, `${JSON.stringify(this.state, null, 2)}\n`, { mode: 0o600 });
    renameSync(temporary, this.path);
  }
}
