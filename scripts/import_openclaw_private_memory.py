"""Import OpenClaw private memory markdown into Honcho observations.

This is intentionally a narrow one-shot/backfill tool. It imports
``memory/private/*.md`` as explicit observations with source metadata and
embeddings, without printing private file contents.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import tiktoken
from sqlalchemy import select

# Match the path-shim convention used by the other scripts in this directory
# so `src.*` imports resolve when the script is run directly.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src import models  # noqa: E402
from src.db import SessionLocal  # noqa: E402
from src.embedding_client import embedding_client  # noqa: E402

DEFAULT_ROOT = Path("/home/bex/.openclaw/workspace/memory/private")
SOURCE_SYSTEM = "openclaw-memory-private"


@dataclass(frozen=True)
class Chunk:
    path: Path
    rel_path: str
    file_sha256: str
    chunk_index: int
    chunk_count: int
    start_line: int
    end_line: int
    content: str
    import_key: str


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chunk_markdown(path: Path, root: Path, *, max_tokens: int) -> list[Chunk]:
    content = path.read_text(encoding="utf-8").replace("\x00", "")
    lines = content.splitlines()
    encoding = tiktoken.get_encoding("o200k_base")
    rel_path = path.relative_to(root.parent.parent).as_posix()
    file_sha = read_file_sha(path)

    raw_chunks: list[tuple[int, int, str]] = []
    active: list[str] = []
    start_line = 1

    def flush(end_line: int) -> None:
        nonlocal active, start_line
        text = "\n".join(active).strip()
        if text:
            raw_chunks.append((start_line, end_line, text))
        active = []
        start_line = end_line + 1

    for line_no, line in enumerate(lines, start=1):
        candidate = "\n".join([*active, line]).strip()
        if active and len(encoding.encode(candidate)) > max_tokens:
            flush(line_no - 1)
        if not active:
            start_line = line_no
        active.append(line)
    if active:
        flush(len(lines))

    chunk_count = len(raw_chunks)
    chunks: list[Chunk] = []
    for index, (start, end, text) in enumerate(raw_chunks):
        chunk_hash = sha256_text(text)
        import_key = sha256_text(f"{SOURCE_SYSTEM}:{rel_path}:{file_sha}:{index}:{chunk_hash}")
        chunks.append(
            Chunk(
                path=path,
                rel_path=rel_path,
                file_sha256=file_sha,
                chunk_index=index,
                chunk_count=chunk_count,
                start_line=start,
                end_line=end,
                content=text,
                import_key=import_key,
            )
        )
    return chunks


async def existing_import_keys(workspace: str) -> set[str]:
    async with SessionLocal() as db:
        result = await db.execute(
            select(models.Document.internal_metadata["import_key"].as_string()).where(
                models.Document.workspace_name == workspace,
                models.Document.internal_metadata["source_system"].as_string()
                == SOURCE_SYSTEM,
                models.Document.deleted_at.is_(None),
            )
        )
        return {key for key in result.scalars().all() if key}


async def import_chunks(
    chunks: list[Chunk],
    *,
    workspace: str,
    observer: str,
    observed: str,
    apply: bool,
) -> dict[str, object]:
    seen = await existing_import_keys(workspace)
    pending = [chunk for chunk in chunks if chunk.import_key not in seen]
    by_path: dict[str, dict[str, int]] = {}
    for chunk in chunks:
        stats = by_path.setdefault(chunk.rel_path, {"total": 0, "new": 0})
        stats["total"] += 1
        if chunk.import_key not in seen:
            stats["new"] += 1

    if not apply or not pending:
        return {
            "apply": apply,
            "total_chunks": len(chunks),
            "new_chunks": len(pending),
            "existing_chunks": len(chunks) - len(pending),
            "by_path": by_path,
            "created": 0,
        }

    embeddings = await embedding_client.simple_batch_embed([chunk.content for chunk in pending])
    imported_at = datetime.now(UTC).isoformat()

    async with SessionLocal() as db:
        workspace_model = (
            await db.execute(
                select(models.Workspace).where(models.Workspace.name == workspace)
            )
        ).scalar_one_or_none()
        if workspace_model is None:
            db.add(models.Workspace(name=workspace))

        for peer_name in {observer, observed}:
            peer = (
                await db.execute(
                    select(models.Peer).where(
                        models.Peer.workspace_name == workspace,
                        models.Peer.name == peer_name,
                    )
                )
            ).scalar_one_or_none()
            if peer is None:
                db.add(models.Peer(workspace_name=workspace, name=peer_name))

        collection = (
            await db.execute(
                select(models.Collection).where(
                    models.Collection.workspace_name == workspace,
                    models.Collection.observer == observer,
                    models.Collection.observed == observed,
                )
            )
        ).scalar_one_or_none()
        if collection is None:
            db.add(
                models.Collection(
                    workspace_name=workspace,
                    observer=observer,
                    observed=observed,
                )
            )
        await db.flush()

        docs: list[models.Document] = []
        for chunk, embedding in zip(pending, embeddings, strict=True):
            doc = models.Document(
                workspace_name=workspace,
                observer=observer,
                observed=observed,
                content=chunk.content,
                level="explicit",
                times_derived=1,
                session_name=None,
                embedding=embedding,
                sync_state="synced",
                last_sync_at=datetime.now(UTC),
                sync_attempts=0,
                internal_metadata={
                    "source_system": SOURCE_SYSTEM,
                    "source_lane": "private",
                    "source_path": chunk.rel_path,
                    "file_sha256": chunk.file_sha256,
                    "chunk_index": chunk.chunk_index,
                    "chunk_count": chunk.chunk_count,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "import_key": chunk.import_key,
                    "imported_at": imported_at,
                    "message_ids": [],
                    "message_created_at": imported_at,
                },
            )
            docs.append(doc)

        db.add_all(docs)
        await db.commit()

    return {
        "apply": apply,
        "total_chunks": len(chunks),
        "new_chunks": len(pending),
        "existing_chunks": len(chunks) - len(pending),
        "by_path": by_path,
        "created": len(pending),
        "observer": observer,
        "observed": observed,
        "workspace": workspace,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--workspace", default="openclaw")
    parser.add_argument("--observer", default="agent-sky")
    parser.add_argument("--observed", default="agent-sky")
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    paths = sorted(args.root.glob("*.md"))
    chunks = [
        chunk
        for path in paths
        for chunk in chunk_markdown(path, args.root, max_tokens=args.max_tokens)
    ]
    result = await import_chunks(
        chunks,
        workspace=args.workspace,
        observer=args.observer,
        observed=args.observed,
        apply=args.apply,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
