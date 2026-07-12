import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createClient, type HonchoConfig } from "./config.js";
import { createServer } from "./server.js";

const config: HonchoConfig = {
  apiKey: process.env.HONCHO_API_KEY?.trim() || "local-dev",
  baseUrl: process.env.HONCHO_API_URL?.trim() || "http://127.0.0.1:8000",
  workspaceId: process.env.HONCHO_WORKSPACE_ID?.trim() || "openclaw",
  userName: process.env.HONCHO_USER_NAME?.trim() || "codex",
  assistantName: process.env.HONCHO_ASSISTANT_NAME?.trim() || "codex",
};

const baseInstructions = readFileSync(
  fileURLToPath(new URL("../instructions.md", import.meta.url)),
  "utf8",
);
const instructions = `${baseInstructions}\n\n## Local Codex identity\n\nUse \`${config.userName}\` as the default peer when a tool requires a peer ID and the user has not requested another peer.`;

const server = createServer({ honcho: createClient(config), config }, instructions);
await server.connect(new StdioServerTransport());
