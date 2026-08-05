#!/usr/bin/env python3
"""Preflight, apply, or verify the canonical Honcho ``fam`` workspace migration."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import SessionLocal  # noqa: E402
from src.operations.fam_migration import (  # noqa: E402
    FamMigrationError,
    apply_migration,
    build_plan,
    load_manifest,
    verify_migration,
)


def _require_backup(path: Path | None) -> None:
    if path is None:
        raise FamMigrationError("--apply requires --backup-file")
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise FamMigrationError(f"backup file does not exist: {path}") from exc
    if not path.is_file() or stat.st_size <= 0:
        raise FamMigrationError(f"backup file is empty or not a regular file: {path}")


async def _run(args: argparse.Namespace) -> dict[str, object]:
    manifest = load_manifest(args.manifest)

    if args.apply:
        if not args.acknowledge_writers_stopped:
            raise FamMigrationError("--apply requires --acknowledge-writers-stopped")
        _require_backup(args.backup_file)
        async with SessionLocal() as db:
            async with db.begin():
                result = await apply_migration(db, manifest)
            return result.report

    if args.verify:
        async with SessionLocal() as db:
            return await verify_migration(db, manifest)

    async with SessionLocal() as db:
        plan = await build_plan(db, manifest)
        await db.rollback()
        return plan.report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument("--acknowledge-writers-stopped", action="store_true")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args()

    report = asyncio.run(_run(args))
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.report_json:
        args.report_json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FamMigrationError as exc:
        raise SystemExit(f"migrate_fam_workspace: {exc}") from exc
