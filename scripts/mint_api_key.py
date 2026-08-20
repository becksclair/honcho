#!/usr/bin/env uv run python
"""
Mint a long-lived API key (JWT) for Honcho MCP access without OAuth.

Convenience alias for `scripts/generate_jwt.py --mcp`. Runs outside the
honcho docker containers -- signs directly with AUTH_JWT_SECRET from env /
.env / config.toml [auth] JWT_SECRET.

Examples:
    # Default MCP key (workspace openclaw, 1y expiry)
    uv run python scripts/mint_api_key.py

    # Custom workspace/peer, 1y
    uv run python scripts/mint_api_key.py --workspace my-workspace --peer my-agent

    # Custom expiry, env format for MCP config
    uv run python scripts/mint_api_key.py --expires 90d --format env

    # Passthrough to generate_jwt.py (see --help there for full options)
    uv run python scripts/mint_api_key.py --workspace openclaw --session my-session --expires 30d --format json
"""

import os
import sys

# Allow running from repo root without installing (mirrors generate_jwt.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Reuse generate_jwt's implementation -- no duplicated JWT logic.
# Inject --mcp before delegating so bare `mint_api_key.py` mints an MCP key,
# while explicit flags still override the defaults inside generate_jwt.py.
if "--help" not in sys.argv and "-h" not in sys.argv and "--mcp" not in sys.argv:
    sys.argv.insert(1, "--mcp")

from scripts.generate_jwt import main as _main  # noqa: E402

if __name__ == "__main__":
    _main()
