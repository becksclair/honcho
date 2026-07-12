import { createHash, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import type { Response } from "express";
import type { OAuthRegisteredClientsStore } from "@modelcontextprotocol/sdk/server/auth/clients.js";
import type { AuthorizationParams, OAuthServerProvider } from "@modelcontextprotocol/sdk/server/auth/provider.js";
import { AccessDeniedError, InvalidGrantError, InvalidRequestError, InvalidTokenError } from "@modelcontextprotocol/sdk/server/auth/errors.js";
import type { AuthInfo } from "@modelcontextprotocol/sdk/server/auth/types.js";
import type { OAuthClientInformationFull, OAuthTokenRevocationRequest, OAuthTokens } from "@modelcontextprotocol/sdk/shared/auth.js";
import { checkResourceAllowed, resourceUrlFromServerUrl } from "@modelcontextprotocol/sdk/shared/auth-utils.js";
import type { OAuthStateStore, StoredOAuthToken } from "./oauth-store.js";

export interface OAuthConfig {
  ownerToken: string;
  allowedRedirectHosts: string[];
  scopes: string[];
  accessTokenTtlSeconds: number;
  refreshTokenTtlSeconds: number;
}

interface CodeRecord {
  clientId: string;
  params: AuthorizationParams;
  expiresAt: number;
}

function safeEqual(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function redirectAllowed(uri: string, hosts: string[]): boolean {
  try {
    const url = new URL(uri);
    return hosts.includes(url.hostname);
  } catch {
    return false;
  }
}

function approvalPage(client: OAuthClientInformationFull, params: AuthorizationParams, error?: string): string {
  const fields: Record<string, string | undefined> = {
    response_type: "code",
    client_id: client.client_id,
    redirect_uri: params.redirectUri,
    code_challenge: params.codeChallenge,
    code_challenge_method: "S256",
    scope: params.scopes?.join(" "),
    state: params.state,
    resource: params.resource?.href,
  };
  const hidden = Object.entries(fields).filter((entry): entry is [string, string] => entry[1] !== undefined)
    .map(([name, value]) => `<input type="hidden" name="${escapeHtml(name)}" value="${escapeHtml(value)}">`).join("\n");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Connect Honcho</title><style>body{font-family:system-ui;background:#0f172a;color:#e2e8f0;margin:0}main{max-width:440px;margin:12vh auto;padding:32px;background:#111827;border:1px solid #334155;border-radius:18px}input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px;border-radius:10px}input{background:#020617;color:#e2e8f0;border:1px solid #475569}button{border:0;background:#38bdf8;font-weight:700}.error{color:#fecaca}</style></head><body><main><h1>Connect Honcho</h1><p>Authorize ${escapeHtml(client.client_name ?? client.client_id)} to use the owner's Honcho memory as Sarah.</p>${error ? `<p class="error">${escapeHtml(error)}</p>` : ""}<form method="post">${hidden}<label for="owner_token">Owner password</label><input id="owner_token" name="owner_token" type="password" required autofocus><button type="submit">Authorize Honcho</button></form></main></body></html>`;
}

export class OAuthClientsStore implements OAuthRegisteredClientsStore {
  constructor(private readonly hosts: string[], private readonly store: OAuthStateStore) {}
  getClient(clientId: string): OAuthClientInformationFull | undefined { return this.store.getClient(clientId); }
  registerClient(client: Omit<OAuthClientInformationFull, "client_id" | "client_id_issued_at">): OAuthClientInformationFull {
    if (!client.redirect_uris.every((uri) => redirectAllowed(uri, this.hosts))) {
      throw new InvalidRequestError("Client redirect_uri is not allowed for this Honcho server");
    }
    const result: OAuthClientInformationFull = {
      ...client,
      client_id: `honcho-${randomUUID()}`,
      client_id_issued_at: Math.floor(Date.now() / 1000),
      token_endpoint_auth_method: client.token_endpoint_auth_method ?? "none",
      grant_types: client.grant_types ?? ["authorization_code", "refresh_token"],
      response_types: client.response_types ?? ["code"],
    };
    this.store.saveClient(result);
    return result;
  }
}

export class SingleUserOAuthProvider implements OAuthServerProvider {
  readonly clientsStore: OAuthRegisteredClientsStore;
  private readonly codes = new Map<string, CodeRecord>();
  private readonly resourceUrl: URL;

  constructor(private readonly config: OAuthConfig, mcpUrl: URL, private readonly store: OAuthStateStore) {
    this.resourceUrl = resourceUrlFromServerUrl(mcpUrl);
    this.clientsStore = new OAuthClientsStore(config.allowedRedirectHosts, store);
  }

  async authorize(client: OAuthClientInformationFull, params: AuthorizationParams, res: Response): Promise<void> {
    if (!params.resource || !checkResourceAllowed({ requestedResource: params.resource, configuredResource: this.resourceUrl })) throw new InvalidRequestError("Invalid or missing OAuth resource");
    if (!(params.scopes ?? []).every((scope) => this.config.scopes.includes(scope))) throw new InvalidRequestError("Requested scope is not supported");
    if (res.req.method !== "POST") {
      res.type("html").send(approvalPage(client, params));
      return;
    }
    if (!safeEqual(String(res.req.body?.owner_token ?? ""), this.config.ownerToken)) {
      res.status(401).type("html").send(approvalPage(client, params, "The owner password was not accepted."));
      return;
    }
    const code = `code-${randomUUID()}`;
    this.codes.set(code, { clientId: client.client_id, params, expiresAt: Date.now() + 300_000 });
    const redirect = new URL(params.redirectUri);
    redirect.searchParams.set("code", code);
    if (params.state !== undefined) redirect.searchParams.set("state", params.state);
    res.redirect(302, redirect.href);
  }

  async challengeForAuthorizationCode(client: OAuthClientInformationFull, code: string): Promise<string> { return this.validCode(client, code).params.codeChallenge; }
  async exchangeAuthorizationCode(client: OAuthClientInformationFull, code: string, _verifier?: string, redirectUri?: string, resource?: URL): Promise<OAuthTokens> {
    const record = this.validCode(client, code);
    if (redirectUri && redirectUri !== record.params.redirectUri) throw new InvalidGrantError("redirect_uri does not match");
    if (resource && !checkResourceAllowed({ requestedResource: resource, configuredResource: this.resourceUrl })) throw new InvalidGrantError("Invalid resource");
    this.codes.delete(code);
    return this.issue(client.client_id, record.params.scopes ?? this.config.scopes, record.params.resource);
  }
  async exchangeRefreshToken(client: OAuthClientInformationFull, refreshToken: string, scopes?: string[], resource?: URL): Promise<OAuthTokens> {
    const hash = hashToken(refreshToken);
    const record = this.store.getToken(hash, "refresh");
    if (!record || record.clientId !== client.client_id) throw new InvalidGrantError("Invalid refresh token");
    if (resource && !checkResourceAllowed({ requestedResource: resource, configuredResource: this.resourceUrl })) throw new InvalidGrantError("Invalid resource");
    const requested = scopes ?? record.scopes;
    if (!requested.every((scope) => record.scopes.includes(scope))) throw new AccessDeniedError("Refresh token cannot grant requested scopes");
    this.store.deleteToken(hash, "refresh");
    return this.issue(client.client_id, requested, resource ?? record.resource);
  }
  async verifyAccessToken(token: string): Promise<AuthInfo> {
    const record = this.store.getToken(hashToken(token), "access");
    if (!record) throw new InvalidTokenError("Invalid or expired access token");
    return { token, clientId: record.clientId, scopes: record.scopes, expiresAt: record.expiresAt, resource: record.resource };
  }
  async revokeToken(_client: OAuthClientInformationFull, request: OAuthTokenRevocationRequest): Promise<void> { this.store.deleteToken(hashToken(request.token)); }

  private validCode(client: OAuthClientInformationFull, code: string): CodeRecord {
    const record = this.codes.get(code);
    if (!record || record.clientId !== client.client_id || record.expiresAt < Date.now()) {
      this.codes.delete(code);
      throw new InvalidGrantError("Invalid authorization code");
    }
    return record;
  }
  private issue(clientId: string, scopes: string[], resource?: URL): OAuthTokens {
    const now = Math.floor(Date.now() / 1000);
    const access = randomBytes(32).toString("base64url");
    const refresh = randomBytes(32).toString("base64url");
    const records: StoredOAuthToken[] = [
      { tokenHash: hashToken(access), tokenType: "access", clientId, scopes, expiresAt: now + this.config.accessTokenTtlSeconds, resource },
      { tokenHash: hashToken(refresh), tokenType: "refresh", clientId, scopes, expiresAt: now + this.config.refreshTokenTtlSeconds, resource },
    ];
    records.forEach((record) => this.store.saveToken(record));
    return { access_token: access, token_type: "bearer", expires_in: this.config.accessTokenTtlSeconds, refresh_token: refresh, scope: scopes.join(" ") };
  }
}

function hashToken(token: string): string { return createHash("sha256").update(token).digest("base64url"); }
