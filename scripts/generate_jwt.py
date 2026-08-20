#!/usr/bin/env uv run python
"""
Utility script to generate scoped JWTs for Honcho.

Runs outside the honcho docker containers -- it signs directly with
AUTH_JWT_SECRET (from env / .env / config.toml [auth] JWT_SECRET) so no
running API or DB is required. Use for minting long-lived MCP keys without
OAuth.

Examples:
    # Admin JWT (no expiry)
    uv run python scripts/generate_jwt.py --admin

    # Admin JWT expiring in 24 hours
    uv run python scripts/generate_jwt.py --admin --expires 24h

    # Workspace-scoped JWT expiring in 30 days
    uv run python scripts/generate_jwt.py --workspace my-workspace --expires 30d

    # Peer-scoped JWT expiring in 1 year
    uv run python scripts/generate_jwt.py --workspace my-workspace --peer my-peer --expires 1y

    # Session-scoped JWT
    uv run python scripts/generate_jwt.py --workspace my-workspace --session my-session --expires 8h

    # Long-lived MCP key (outside docker, no OAuth) -- defaults to workspace openclaw, 1y expiry
    uv run python scripts/generate_jwt.py --mcp
    uv run python scripts/generate_jwt.py --mcp --workspace my-workspace --peer my-agent --expires 1y --format env

    # Convenience alias (same as --mcp above)
    uv run python scripts/mint_api_key.py --workspace openclaw --peer codex --expires 1y
"""

import argparse
import datetime
import json
import os
import re
import sys

# Allow running from repo root without installing
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import settings
from src.security import JWTParams, create_jwt, scope_requires_workspace
from src.utils.formatting import format_datetime_utc

DURATION_UNITS = {
    "s": datetime.timedelta(seconds=1),
    "m": datetime.timedelta(minutes=1),
    "h": datetime.timedelta(hours=1),
    "d": datetime.timedelta(days=1),
    "w": datetime.timedelta(weeks=1),
    "y": datetime.timedelta(days=365),
}


def parse_duration(value: str) -> datetime.timedelta:
    """Parse a duration string like '5h', '1d', '2w', '1y' into a timedelta."""
    match = re.fullmatch(r"(\d+)([smhdwy])", value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid duration '{value}'. Use format like: 30s, 5m, 2h, 7d, 2w, 1y"
        )
    amount, unit = int(match.group(1)), match.group(2)
    return DURATION_UNITS[unit] * amount


def main():
    parser = argparse.ArgumentParser(
        description="Generate a scoped JWT for Honcho authentication. "
        "Runs outside docker -- signs with AUTH_JWT_SECRET from env/.env/config.toml.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Generate an admin JWT (full access)",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        metavar="NAME",
        help="Scope the JWT to a workspace",
    )
    parser.add_argument(
        "--peer",
        "-p",
        metavar="NAME",
        help="Scope the JWT to a peer (requires --workspace)",
    )
    parser.add_argument(
        "--session",
        "-s",
        metavar="NAME",
        help="Scope the JWT to a session (requires --workspace)",
    )
    parser.add_argument(
        "--expires",
        "-e",
        metavar="DURATION",
        type=parse_duration,
        help="Token expiry duration. Units: s=seconds, m=minutes, h=hours, d=days, w=weeks, y=years. E.g. 5h, 30d, 1y",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="MCP shortcut: defaults to --workspace openclaw --expires 1y when those "
        "flags are not set. Intended for minting long-lived HONCHO_API_KEY values "
        "outside docker without OAuth. Explicit --workspace/--expires override the defaults.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "raw", "env", "json"],
        default=None,
        help="Output format: text (default, human-readable), raw (token only), "
        "env (HONCHO_API_KEY=... line for MCP config), json (machine-readable). "
        "If omitted, defaults to text unless --print-only is given.",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Only print the token, no labels (deprecated: use --format raw)",
    )
    args = parser.parse_args()

    # --mcp sugar: fill workspace/expires defaults before validation
    if args.mcp:
        if args.admin:
            parser.error("--mcp cannot be combined with --admin")
        if not args.workspace:
            args.workspace = "openclaw"
        if not args.expires:
            args.expires = parse_duration("1y")

    # Resolve --print-only / --format interop (keep --print-only as alias for raw)
    fmt = args.format
    if fmt is None:
        fmt = "raw" if args.print_only else "text"
    elif args.print_only and fmt != "raw":
        parser.error(
            "--print-only is an alias for --format raw; do not combine with a different --format"
        )

    if not args.admin and not any([args.workspace, args.peer, args.session]):
        parser.error(
            "Specify --admin or at least one of --workspace, --peer, --session (or use --mcp)"
        )

    if args.admin and any([args.workspace, args.peer, args.session]):
        parser.error(
            "--admin cannot be combined with --workspace, --peer, or --session"
        )

    if scope_requires_workspace(
        peer=args.peer, session=args.session, workspace=args.workspace
    ):
        parser.error("--peer and --session require --workspace")

    exp_str: str | None = None
    if args.expires:
        expiry = datetime.datetime.now(datetime.timezone.utc) + args.expires
        exp_str = format_datetime_utc(expiry)

    params = JWTParams(
        ad=True if args.admin else None,
        w=args.workspace,
        p=args.peer,
        s=args.session,
        exp=exp_str,
    )

    try:
        token = create_jwt(params)
    except ValueError as exc:
        # AUTH_JWT_SECRET missing -- give actionable hint
        if "JWT_SECRET" in str(exc) or not settings.AUTH.JWT_SECRET:
            parser.error(
                "AUTH_JWT_SECRET is not set, cannot create JWT.\n"
                "Set it via one of:\n"
                "  export AUTH_JWT_SECRET=$(uv run python scripts/generate_jwt_secret.py --print-only)\n"
                "  echo AUTH_JWT_SECRET=<secret> >> .env\n"
                '  [auth] JWT_SECRET = "<secret>" in config.toml\n'
                "The secret must match the value mounted into the honcho API/deriver "
                "containers (see /opt/homelab/compose.yaml or docker-compose.yml)."
            )
        raise

    if fmt == "raw":
        print(token)
        return
    if fmt == "env":
        print(f"HONCHO_API_KEY={token}")
        if args.mcp or args.workspace:
            # Hint for stdio MCP -- not part of machine output, to stderr
            print(
                f"# MCP stdio: HONCHO_API_KEY above, plus HONCHO_API_URL "
                f"(default http://127.0.0.1:8000) and HONCHO_WORKSPACE_ID={args.workspace}",
                file=sys.stderr,
            )
        return
    if fmt == "json":
        payload = {
            "token": token,
            "scope": {
                "admin": bool(args.admin),
                "workspace": args.workspace,
                "peer": args.peer,
                "session": args.session,
            },
            "expires_at": exp_str,
            "expires_in": str(args.expires) if args.expires else None,
        }
        print(json.dumps(payload, indent=2))
        return

    # fmt == "text"
    scope_parts: list[str] = []
    if args.admin:
        scope_parts.append("admin")
    if args.workspace:
        scope_parts.append(f"workspace={args.workspace}")
    if args.peer:
        scope_parts.append(f"peer={args.peer}")
    if args.session:
        scope_parts.append(f"session={args.session}")

    print(f"Scope:   {', '.join(scope_parts)}")
    if exp_str:
        print(f"Expires: {exp_str}")
    else:
        print("Expires: never")
    print(f"Token:   {token}")


if __name__ == "__main__":
    main()
