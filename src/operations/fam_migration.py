# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportImplicitStringConcatenation=false
"""Manifest-driven migration into the canonical ``fam`` workspace.

The operation moves the live family corpus in place so message/document IDs,
embeddings, and reasoning-tree source IDs remain stable. Source workspaces are
kept as archives for explicitly excluded sessions and rollback remains a full
Postgres restore from the operator-provided pre-cutover dump.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nanoid import generate as generate_nanoid
from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import models

CANONICAL_PEERS = ("bex", "sarah", "sky", "luke", "kate")
LOCK_TABLES = (
    "active_queue_sessions",
    "queue_dead_letter",
    "queue",
    "message_embeddings",
    "messages",
    "documents",
    "collections",
    "session_peers",
    "sessions",
    "peers",
    "webhook_endpoints",
    "workspaces",
)


class FamMigrationError(RuntimeError):
    """Raised when preflight or migration invariants fail."""


@dataclass(frozen=True)
class SourceWorkspace:
    name: str
    peer_map: dict[str, str]
    leave_sessions: frozenset[str]


@dataclass(frozen=True)
class FamManifest:
    target_workspace: str
    canonical_peers: dict[str, dict[str, Any]]
    workspace_metadata: dict[str, Any]
    workspace_configuration: dict[str, Any]
    sources: tuple[SourceWorkspace, ...]
    peer_card_authority: dict[str, tuple[str, str]]

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(source.name for source in self.sources)

    def source(self, workspace: str) -> SourceWorkspace:
        for source in self.sources:
            if source.name == workspace:
                return source
        raise FamMigrationError(f"workspace {workspace!r} is not in the manifest")


@dataclass(frozen=True)
class SessionMove:
    source_workspace: str
    session_name: str
    old_session_id: str
    new_session_id: str


@dataclass(frozen=True)
class MigrationPlan:
    sessions: tuple[SessionMove, ...]
    collection_pairs: tuple[tuple[str, str], ...]
    report: dict[str, Any]

    @property
    def session_ids(self) -> dict[str, str]:
        return {move.old_session_id: move.new_session_id for move in self.sessions}


@dataclass(frozen=True)
class MigrationResult:
    target_workspace: str
    report: dict[str, Any]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FamMigrationError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FamMigrationError(f"invalid manifest JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise FamMigrationError("manifest root must be an object")
    return value


def load_manifest(path: Path) -> FamManifest:
    raw = _load_json(path)
    if raw.get("schema_version") != 1:
        raise FamMigrationError("unsupported manifest schema_version")
    target = raw.get("target_workspace")
    if target != "fam":
        raise FamMigrationError("target_workspace must be 'fam'")

    canonical = raw.get("canonical_peers")
    if not isinstance(canonical, dict) or tuple(sorted(canonical)) != tuple(
        sorted(CANONICAL_PEERS)
    ):
        raise FamMigrationError("manifest must define exactly five canonical peers")

    metadata = raw.get("workspace_metadata")
    configuration = raw.get("workspace_configuration")
    if not isinstance(metadata, dict) or not isinstance(configuration, dict):
        raise FamMigrationError("workspace metadata/configuration must be objects")

    identity = configuration.get("identity")
    dialectic = configuration.get("dialectic")
    if not isinstance(identity, dict) or not isinstance(dialectic, dict):
        raise FamMigrationError("manifest lacks identity/dialectic configuration")
    if (
        identity.get("strict") is not True
        or identity.get("allow_dynamic_peers") is not False
    ):
        raise FamMigrationError(
            "fam identity must be strict with dynamic peers disabled"
        )
    if dialectic.get("recall_scope") != "workspace" or dialectic.get(
        "recall_observers"
    ) != list(CANONICAL_PEERS):
        raise FamMigrationError("fam dialectic configuration is not canonical")

    source_raw = raw.get("source_workspaces")
    if not isinstance(source_raw, dict) or not source_raw:
        raise FamMigrationError("manifest has no source_workspaces")
    sources: list[SourceWorkspace] = []
    for workspace, spec in source_raw.items():
        if not isinstance(workspace, str) or not isinstance(spec, dict):
            raise FamMigrationError("invalid source workspace entry")
        peer_map = spec.get("peer_map")
        leave_sessions = spec.get("leave_sessions", [])
        if not isinstance(peer_map, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in peer_map.items()
        ):
            raise FamMigrationError(f"invalid peer_map for {workspace}")
        if not set(peer_map.values()).issubset(CANONICAL_PEERS):
            raise FamMigrationError(f"peer_map for {workspace} has non-family targets")
        if not isinstance(leave_sessions, list) or not all(
            isinstance(name, str) for name in leave_sessions
        ):
            raise FamMigrationError(f"invalid leave_sessions for {workspace}")
        sources.append(
            SourceWorkspace(
                name=workspace,
                peer_map=dict(peer_map),
                leave_sessions=frozenset(leave_sessions),
            )
        )

    authority_raw = raw.get("peer_card_authority")
    if not isinstance(authority_raw, dict) or set(authority_raw) != set(
        CANONICAL_PEERS
    ):
        raise FamMigrationError("peer_card_authority must cover every canonical peer")
    authority: dict[str, tuple[str, str]] = {}
    for target_peer, spec in authority_raw.items():
        if not isinstance(spec, dict):
            raise FamMigrationError(f"invalid peer card authority for {target_peer}")
        workspace = spec.get("workspace")
        peer = spec.get("peer")
        if not isinstance(workspace, str) or not isinstance(peer, str):
            raise FamMigrationError(f"invalid peer card authority for {target_peer}")
        source = next((item for item in sources if item.name == workspace), None)
        if source is None or source.peer_map.get(peer) != target_peer:
            raise FamMigrationError(
                f"peer card authority {workspace}/{peer} does not map to {target_peer}"
            )
        authority[target_peer] = (workspace, peer)

    return FamManifest(
        target_workspace=target,
        canonical_peers=copy.deepcopy(canonical),
        workspace_metadata=copy.deepcopy(metadata),
        workspace_configuration=copy.deepcopy(configuration),
        sources=tuple(sources),
        peer_card_authority=authority,
    )


async def _scalar(db: AsyncSession, sql: str, **params: Any) -> Any:
    return await db.scalar(text(sql), params)


async def _mappings(db: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = await db.execute(text(sql), params)
    return [dict(row) for row in result.mappings().all()]


async def _workspace_exists(db: AsyncSession, workspace: str) -> bool:
    return bool(
        await _scalar(
            db,
            "SELECT EXISTS(SELECT 1 FROM workspaces WHERE name=:workspace)",
            workspace=workspace,
        )
    )


def _selected_sessions(
    rows: list[dict[str, Any]], source: SourceWorkspace
) -> list[dict[str, Any]]:
    names = {str(row["name"]) for row in rows}
    missing_leave = source.leave_sessions - names
    if missing_leave:
        raise FamMigrationError(
            f"{source.name} leave_sessions are missing: {sorted(missing_leave)}"
        )
    return [row for row in rows if row["name"] not in source.leave_sessions]


async def build_plan(db: AsyncSession, manifest: FamManifest) -> MigrationPlan:
    if await _workspace_exists(db, manifest.target_workspace):
        marker = await _scalar(
            db,
            "SELECT metadata->>'famMigrationState' FROM workspaces WHERE name=:workspace",
            workspace=manifest.target_workspace,
        )
        if marker == "complete":
            raise FamMigrationError("fam migration is already complete")
        raise FamMigrationError(
            "target workspace already exists without completion marker"
        )

    missing_sources = [
        source
        for source in manifest.source_names
        if not await _workspace_exists(db, source)
    ]
    if missing_sources:
        raise FamMigrationError(f"source workspaces are missing: {missing_sources}")

    active_count = int(
        await _scalar(db, "SELECT count(*) FROM active_queue_sessions") or 0
    )
    if active_count:
        raise FamMigrationError(
            f"active_queue_sessions is not empty ({active_count}); stop all writers first"
        )

    pending = int(
        await _scalar(
            db,
            "SELECT count(*) FROM queue WHERE processed=false AND workspace_name = ANY(:sources)",
            sources=list(manifest.source_names),
        )
        or 0
    )
    if pending:
        raise FamMigrationError(f"source workspaces have {pending} pending queue tasks")

    session_moves: list[SessionMove] = []
    source_session_rows: dict[str, list[dict[str, Any]]] = {}
    seen_session_names: set[str] = set()
    for source in manifest.sources:
        rows = await _mappings(
            db,
            "SELECT id,name FROM sessions WHERE workspace_name=:workspace ORDER BY name",
            workspace=source.name,
        )
        selected = _selected_sessions(rows, source)
        source_session_rows[source.name] = selected
        for row in selected:
            name = str(row["name"])
            if name in seen_session_names:
                raise FamMigrationError(f"selected session name collision: {name}")
            seen_session_names.add(name)
            session_moves.append(
                SessionMove(
                    source_workspace=source.name,
                    session_name=name,
                    old_session_id=str(row["id"]),
                    new_session_id=generate_nanoid(),
                )
            )

    if seen_session_names:
        target_collisions = int(
            await _scalar(
                db,
                "SELECT count(*) FROM sessions WHERE workspace_name=:target AND name = ANY(:names)",
                target=manifest.target_workspace,
                names=list(seen_session_names),
            )
            or 0
        )
        if target_collisions:
            raise FamMigrationError(
                f"target has {target_collisions} colliding session names"
            )

    sessions_by_source = {
        source.name: [
            move.session_name
            for move in session_moves
            if move.source_workspace == source.name
        ]
        for source in manifest.sources
    }
    selected_ids_by_source = {
        source.name: [
            move.old_session_id
            for move in session_moves
            if move.source_workspace == source.name
        ]
        for source in manifest.sources
    }

    collection_pairs: set[tuple[str, str]] = set()
    report: dict[str, Any] = {
        "target_workspace": manifest.target_workspace,
        "sources": {},
        "sessions": len(session_moves),
        "canonical_peers": len(CANONICAL_PEERS),
    }

    for source in manifest.sources:
        selected_names = sessions_by_source[source.name]
        selected_ids = selected_ids_by_source[source.name]
        mapping = source.peer_map

        member_rows = await _mappings(
            db,
            """
            SELECT DISTINCT peer_name
            FROM session_peers
            WHERE workspace_name=:workspace AND session_name = ANY(:sessions)
            """,
            workspace=source.name,
            sessions=selected_names,
        )
        author_rows = await _mappings(
            db,
            """
            SELECT DISTINCT peer_name
            FROM messages
            WHERE workspace_name=:workspace AND session_name = ANY(:sessions)
            """,
            workspace=source.name,
            sessions=selected_names,
        )
        unknown_peers = sorted(
            {
                str(row["peer_name"])
                for row in [*member_rows, *author_rows]
                if row["peer_name"] not in mapping
            }
        )
        if unknown_peers:
            raise FamMigrationError(
                f"{source.name} selected sessions contain unmapped peers: {unknown_peers}"
            )

        document_pairs = await _mappings(
            db,
            """
            SELECT DISTINCT observer,observed
            FROM documents
            WHERE workspace_name=:workspace
              AND (session_name = ANY(:sessions) OR session_name IS NULL)
            """,
            workspace=source.name,
            sessions=selected_names,
        )
        selected_session_unknown = [
            (str(row["observer"]), str(row["observed"]))
            for row in document_pairs
            if row["observer"] not in mapping or row["observed"] not in mapping
        ]
        if selected_session_unknown:
            # Unknown null-session pairs are archive material and may remain.
            actual_unknown = await _mappings(
                db,
                """
                SELECT DISTINCT observer,observed
                FROM documents
                WHERE workspace_name=:workspace
                  AND session_name = ANY(:sessions)
                  AND (NOT (observer = ANY(:mapped)) OR NOT (observed = ANY(:mapped)))
                """,
                workspace=source.name,
                sessions=selected_names,
                mapped=list(mapping),
            )
            if actual_unknown:
                raise FamMigrationError(
                    f"{source.name} selected-session documents contain unmapped peer pairs: "
                    + repr(
                        [
                            (str(row["observer"]), str(row["observed"]))
                            for row in actual_unknown
                        ]
                    )
                )

        for row in document_pairs:
            observer = mapping.get(str(row["observer"]))
            observed = mapping.get(str(row["observed"]))
            if observer and observed:
                collection_pairs.add((observer, observed))

        counts = await _mappings(
            db,
            """
            SELECT
              (SELECT count(*) FROM messages WHERE workspace_name=:workspace AND session_name = ANY(:sessions)) AS messages,
              (SELECT count(*) FROM documents WHERE workspace_name=:workspace AND (session_name = ANY(:sessions) OR (session_name IS NULL AND observer = ANY(:mapped) AND observed = ANY(:mapped)))) AS documents,
              (SELECT count(*) FROM queue WHERE workspace_name=:workspace AND (session_id = ANY(:session_ids) OR session_id IS NULL)) AS queue_rows,
              (SELECT count(*) FROM queue_dead_letter WHERE workspace_name=:workspace AND session_id = ANY(:session_ids)) AS dead_letters
            """,
            workspace=source.name,
            sessions=selected_names,
            session_ids=selected_ids,
            mapped=list(mapping),
        )
        report["sources"][source.name] = {
            "sessions": len(selected_names),
            "leave_sessions": len(source.leave_sessions),
            **(counts[0] if counts else {}),
        }

    for target_peer, (workspace, source_peer) in manifest.peer_card_authority.items():
        exists = bool(
            await _scalar(
                db,
                "SELECT EXISTS(SELECT 1 FROM peers WHERE workspace_name=:workspace AND name=:peer)",
                workspace=workspace,
                peer=source_peer,
            )
        )
        if not exists:
            raise FamMigrationError(
                f"peer-card authority is missing for {target_peer}: {workspace}/{source_peer}"
            )

    report["collection_pairs"] = len(collection_pairs)
    return MigrationPlan(
        sessions=tuple(session_moves),
        collection_pairs=tuple(sorted(collection_pairs)),
        report=report,
    )


def _canonical_card_key(
    key: str,
    source: SourceWorkspace,
    aliases: dict[str, str],
) -> str | None:
    if key == "peer_card":
        return key
    suffix = "_peer_card"
    if not key.endswith(suffix):
        return None
    raw_peer = key[: -len(suffix)]
    canonical = source.peer_map.get(raw_peer) or aliases.get(raw_peer.casefold())
    if canonical not in CANONICAL_PEERS:
        return None
    return f"{canonical}_peer_card"


def _merge_peer_rows(
    target_peer: str,
    rows: list[dict[str, Any]],
    manifest: FamManifest,
) -> dict[str, Any]:
    authority_workspace, authority_peer = manifest.peer_card_authority[target_peer]
    authority = next(
        (
            row
            for row in rows
            if row["workspace_name"] == authority_workspace
            and row["name"] == authority_peer
        ),
        None,
    )
    if authority is None:
        raise FamMigrationError(f"authority row missing for {target_peer}")

    ordered = sorted(
        rows,
        key=lambda row: (
            row["workspace_name"] == authority_workspace
            and row["name"] == authority_peer,
            str(row["workspace_name"]),
            str(row["name"]),
        ),
    )
    metadata: dict[str, Any] = {}
    configuration: dict[str, Any] = {}
    cards: dict[str, Any] = {}
    aliases = {
        str(key).casefold(): str(value)
        for key, value in manifest.workspace_configuration["identity"][
            "aliases"
        ].items()
    }
    for row in ordered:
        metadata.update(copy.deepcopy(row.get("metadata") or {}))
        configuration.update(copy.deepcopy(row.get("configuration") or {}))
        source = manifest.source(str(row["workspace_name"]))
        internal = row.get("internal_metadata") or {}
        for key, value in internal.items():
            canonical_key = _canonical_card_key(str(key), source, aliases)
            if canonical_key is None:
                continue
            if canonical_key == "peer_card" and row is not authority:
                continue
            cards[canonical_key] = copy.deepcopy(value)

    authority_internal = copy.deepcopy(authority.get("internal_metadata") or {})
    internal_metadata = {
        key: value
        for key, value in authority_internal.items()
        if key != "peer_card" and not key.endswith("_peer_card")
    }
    internal_metadata.update(cards)
    metadata.update(
        {
            "display_name": manifest.canonical_peers[target_peer].get(
                "display_name", target_peer
            ),
            "fam_migration_sources": [
                f"{row['workspace_name']}/{row['name']}" for row in ordered
            ],
        }
    )
    return {
        "id": generate_nanoid(),
        "name": target_peer,
        "metadata": metadata,
        "configuration": configuration,
        "internal_metadata": internal_metadata,
        "created_at": min(row["created_at"] for row in rows),
        "workspace_name": manifest.target_workspace,
    }


def _merge_bool_configuration(configurations: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for configuration in configurations:
        merged.update(copy.deepcopy(configuration))
    for key in ("observe_me", "observe_others"):
        values = [config.get(key) for config in configurations if key in config]
        if any(value is True for value in values):
            merged[key] = True
        elif values and all(value is False for value in values):
            merged[key] = False
    return merged


def _rewrite_payload(
    value: Any,
    *,
    source: SourceWorkspace,
    target_workspace: str,
    session_ids: dict[str, str],
) -> Any:
    peer_keys = {
        "peer",
        "peer_id",
        "peer_name",
        "observer",
        "observed",
        "observer_id",
        "observed_id",
        "assistant_peer_id",
        "user_peer_id",
    }
    workspace_keys = {"workspace", "workspace_id", "workspace_name"}
    session_id_keys = {"session_id"}

    def rewrite(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {str(k): rewrite(v, str(k)) for k, v in node.items()}
        if isinstance(node, list):
            if key == "observers":
                rewritten: list[Any] = []
                for item in node:
                    if isinstance(item, str):
                        target = source.peer_map.get(item)
                        if target is None:
                            raise FamMigrationError(
                                f"queue payload contains unmapped observer {item!r}"
                            )
                        rewritten.append(target)
                    else:
                        rewritten.append(rewrite(item))
                return rewritten
            return [rewrite(item) for item in node]
        if isinstance(node, str):
            if key in peer_keys:
                target = source.peer_map.get(node)
                if target is None:
                    raise FamMigrationError(
                        f"queue payload contains unmapped peer {node!r}"
                    )
                return target
            if key in workspace_keys and node == source.name:
                return target_workspace
            if key in session_id_keys and node in session_ids:
                return session_ids[node]
        return node

    return rewrite(value)


def _rewrite_work_unit_key(
    key: str,
    *,
    source_workspace: str,
    target_workspace: str,
    session_ids: dict[str, str],
) -> str:
    rewritten = key.replace(source_workspace, target_workspace)
    for old_id, new_id in session_ids.items():
        rewritten = rewritten.replace(old_id, new_id)
    return rewritten


async def _lock_tables(db: AsyncSession) -> None:
    await db.execute(
        text("LOCK TABLE " + ", ".join(LOCK_TABLES) + " IN SHARE ROW EXCLUSIVE MODE")
    )


async def _install_temp_maps(
    db: AsyncSession, manifest: FamManifest, plan: MigrationPlan
) -> None:
    await db.execute(
        text(
            """
            CREATE TEMP TABLE fam_peer_map (
              source_workspace text NOT NULL,
              source_peer text NOT NULL,
              target_peer text NOT NULL,
              PRIMARY KEY (source_workspace, source_peer)
            ) ON COMMIT DROP
            """
        )
    )
    await db.execute(
        text(
            """
            CREATE TEMP TABLE fam_session_map (
              source_workspace text NOT NULL,
              source_session text NOT NULL,
              old_session_id text NOT NULL,
              new_session_id text NOT NULL,
              PRIMARY KEY (source_workspace, source_session),
              UNIQUE (old_session_id),
              UNIQUE (new_session_id)
            ) ON COMMIT DROP
            """
        )
    )
    peer_rows = [
        {
            "source_workspace": source.name,
            "source_peer": source_peer,
            "target_peer": target_peer,
        }
        for source in manifest.sources
        for source_peer, target_peer in source.peer_map.items()
    ]
    await db.execute(
        text(
            "INSERT INTO fam_peer_map(source_workspace,source_peer,target_peer) "
            "VALUES (:source_workspace,:source_peer,:target_peer)"
        ),
        peer_rows,
    )
    await db.execute(
        text(
            "INSERT INTO fam_session_map(source_workspace,source_session,old_session_id,new_session_id) "
            "VALUES (:source_workspace,:source_session,:old_session_id,:new_session_id)"
        ),
        [
            {
                "source_workspace": move.source_workspace,
                "source_session": move.session_name,
                "old_session_id": move.old_session_id,
                "new_session_id": move.new_session_id,
            }
            for move in plan.sessions
        ],
    )


async def _create_target_workspace_and_peers(
    db: AsyncSession, manifest: FamManifest
) -> None:
    metadata = copy.deepcopy(manifest.workspace_metadata)
    metadata.update(
        {
            "famMigrationState": "running",
            "famMigrationSources": list(manifest.source_names),
        }
    )
    await db.execute(
        insert(models.Workspace.__table__),
        {
            "id": generate_nanoid(),
            "name": manifest.target_workspace,
            "metadata": metadata,
            "configuration": manifest.workspace_configuration,
            "internal_metadata": {"fam_migration": {"state": "running"}},
        },
    )

    source_rows = await _mappings(
        db,
        """
        SELECT p.*
        FROM peers p
        JOIN fam_peer_map pm
          ON pm.source_workspace=p.workspace_name AND pm.source_peer=p.name
        ORDER BY p.workspace_name,p.name
        """,
    )
    rows_by_target: dict[str, list[dict[str, Any]]] = {
        peer: [] for peer in CANONICAL_PEERS
    }
    source_lookup = {
        (source.name, source_peer): target
        for source in manifest.sources
        for source_peer, target in source.peer_map.items()
    }
    for row in source_rows:
        target = source_lookup[(str(row["workspace_name"]), str(row["name"]))]
        rows_by_target[target].append(row)
    missing = [peer for peer, rows in rows_by_target.items() if not rows]
    if missing:
        raise FamMigrationError(f"canonical peers lack source rows: {missing}")

    peer_rows = [
        _merge_peer_rows(peer, rows_by_target[peer], manifest)
        for peer in CANONICAL_PEERS
    ]
    await db.execute(insert(models.Peer.__table__), peer_rows)


async def _create_target_sessions(db: AsyncSession, manifest: FamManifest) -> None:
    await db.execute(
        text(
            """
            INSERT INTO sessions(
              id,is_active,metadata,created_at,configuration,internal_metadata,
              workspace_name,name
            )
            SELECT
              sm.new_session_id,s.is_active,s.metadata,s.created_at,s.configuration,
              s.internal_metadata || jsonb_build_object(
                'fam_migration', jsonb_build_object(
                  'source_workspace', sm.source_workspace,
                  'source_session_id', sm.old_session_id
                )
              ),
              :target,s.name
            FROM sessions s
            JOIN fam_session_map sm
              ON sm.source_workspace=s.workspace_name AND sm.source_session=s.name
            """
        ),
        {"target": manifest.target_workspace},
    )


async def _create_target_session_peers(db: AsyncSession, manifest: FamManifest) -> None:
    rows = await _mappings(
        db,
        """
        SELECT sp.*,sm.source_workspace,pm.target_peer
        FROM session_peers sp
        JOIN fam_session_map sm
          ON sm.source_workspace=sp.workspace_name AND sm.source_session=sp.session_name
        JOIN fam_peer_map pm
          ON pm.source_workspace=sp.workspace_name AND pm.source_peer=sp.peer_name
        ORDER BY sp.workspace_name,sp.session_name,sp.joined_at,sp.peer_name
        """,
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(
            (str(row["session_name"]), str(row["target_peer"])), []
        ).append(row)

    inserts: list[dict[str, Any]] = []
    for (session_name, target_peer), candidates in grouped.items():
        inserts.append(
            {
                "workspace_name": manifest.target_workspace,
                "session_name": session_name,
                "peer_name": target_peer,
                "configuration": _merge_bool_configuration(
                    [candidate.get("configuration") or {} for candidate in candidates]
                ),
                "internal_metadata": {
                    key: value
                    for candidate in candidates
                    for key, value in (candidate.get("internal_metadata") or {}).items()
                },
                "joined_at": min(candidate["joined_at"] for candidate in candidates),
                "left_at": (
                    None
                    if any(candidate["left_at"] is None for candidate in candidates)
                    else max(candidate["left_at"] for candidate in candidates)
                ),
            }
        )
    if inserts:
        await db.execute(insert(models.session_peers_table), inserts)


async def _create_target_collections(
    db: AsyncSession, manifest: FamManifest, plan: MigrationPlan
) -> None:
    collection_rows = await _mappings(
        db,
        """
        SELECT c.*,po.target_peer AS target_observer,pd.target_peer AS target_observed
        FROM collections c
        JOIN fam_peer_map po
          ON po.source_workspace=c.workspace_name AND po.source_peer=c.observer
        JOIN fam_peer_map pd
          ON pd.source_workspace=c.workspace_name AND pd.source_peer=c.observed
        ORDER BY c.created_at,c.workspace_name,c.observer,c.observed
        """,
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in collection_rows:
        key = (str(row["target_observer"]), str(row["target_observed"]))
        if key in plan.collection_pairs:
            grouped.setdefault(key, []).append(row)

    inserts: list[dict[str, Any]] = []
    for observer, observed in plan.collection_pairs:
        candidates = grouped.get((observer, observed), [])
        metadata: dict[str, Any] = {}
        internal_metadata: dict[str, Any] = {}
        for candidate in candidates:
            metadata.update(copy.deepcopy(candidate.get("metadata") or {}))
            internal_metadata.update(
                copy.deepcopy(candidate.get("internal_metadata") or {})
            )
        inserts.append(
            {
                "id": generate_nanoid(),
                "observer": observer,
                "observed": observed,
                "workspace_name": manifest.target_workspace,
                "metadata": metadata,
                "internal_metadata": internal_metadata,
                "created_at": (
                    min(candidate["created_at"] for candidate in candidates)
                    if candidates
                    else datetime.now().astimezone()
                ),
            }
        )
    if inserts:
        await db.execute(insert(models.Collection.__table__), inserts)


async def _move_corpus(db: AsyncSession, manifest: FamManifest) -> None:
    target = manifest.target_workspace
    await db.execute(
        text(
            """
            UPDATE messages m
            SET workspace_name=:target, peer_name=pm.target_peer
            FROM fam_session_map sm,fam_peer_map pm
            WHERE m.workspace_name=sm.source_workspace
              AND m.session_name=sm.source_session
              AND pm.source_workspace=m.workspace_name
              AND pm.source_peer=m.peer_name
            """
        ),
        {"target": target},
    )
    await db.execute(
        text(
            """
            UPDATE message_embeddings e
            SET workspace_name=:target,peer_name=pm.target_peer
            FROM fam_session_map sm,fam_peer_map pm
            WHERE e.workspace_name=sm.source_workspace
              AND e.session_name=sm.source_session
              AND pm.source_workspace=e.workspace_name
              AND pm.source_peer=e.peer_name
            """
        ),
        {"target": target},
    )
    await db.execute(
        text(
            """
            UPDATE documents d
            SET workspace_name=:target,
                observer=po.target_peer,
                observed=pd.target_peer
            FROM fam_session_map sm,fam_peer_map po,fam_peer_map pd
            WHERE d.workspace_name=sm.source_workspace
              AND d.session_name=sm.source_session
              AND po.source_workspace=d.workspace_name
              AND po.source_peer=d.observer
              AND pd.source_workspace=d.workspace_name
              AND pd.source_peer=d.observed
            """
        ),
        {"target": target},
    )
    await db.execute(
        text(
            """
            UPDATE documents d
            SET workspace_name=:target,
                observer=po.target_peer,
                observed=pd.target_peer
            FROM fam_peer_map po,fam_peer_map pd
            WHERE d.workspace_name=po.source_workspace
              AND d.session_name IS NULL
              AND po.source_peer=d.observer
              AND pd.source_workspace=d.workspace_name
              AND pd.source_peer=d.observed
            """
        ),
        {"target": target},
    )


async def _move_queue_history(
    db: AsyncSession, manifest: FamManifest, plan: MigrationPlan
) -> None:
    session_ids = plan.session_ids
    selected_message_ids = {
        int(row["id"])
        for row in await _mappings(
            db,
            "SELECT id FROM messages WHERE workspace_name=:target",
            target=manifest.target_workspace,
        )
    }
    for table in ("queue", "queue_dead_letter"):
        rows = await _mappings(
            db,
            f"SELECT * FROM {table} WHERE workspace_name = ANY(:sources) ORDER BY id",
            sources=list(manifest.source_names),
        )
        for row in rows:
            source = manifest.source(str(row["workspace_name"]))
            old_session_id_raw = row.get("session_id")
            old_session_id = (
                str(old_session_id_raw) if old_session_id_raw is not None else None
            )
            message_id = row.get("message_id")
            should_move = old_session_id in session_ids if old_session_id else False
            should_move = should_move or (
                message_id is not None and int(message_id) in selected_message_ids
            )
            if old_session_id is None and row.get("task_type") in {"dream", "webhook"}:
                payload = row.get("payload") or {}
                if row.get("task_type") == "webhook":
                    data = payload.get("data")
                    data = data if isinstance(data, dict) else {}
                    observer = data.get("observer")
                    observed = data.get("observed")
                    should_move = (
                        observer is None or observer in source.peer_map
                    ) and (observed is None or observed in source.peer_map)
                else:
                    observer = payload.get("observer")
                    observed = payload.get("observed")
                    should_move = (
                        isinstance(observer, str)
                        and isinstance(observed, str)
                        and observer in source.peer_map
                        and observed in source.peer_map
                    )
            if not should_move:
                continue

            rewritten_payload = _rewrite_payload(
                row["payload"],
                source=source,
                target_workspace=manifest.target_workspace,
                session_ids=session_ids,
            )
            rewritten_key = _rewrite_work_unit_key(
                str(row["work_unit_key"]),
                source_workspace=source.name,
                target_workspace=manifest.target_workspace,
                session_ids=session_ids,
            )
            table_model = (
                models.QueueItem if table == "queue" else models.QueueDeadLetter
            )
            await db.execute(
                update(table_model)
                .where(table_model.id == row["id"])
                .values(
                    workspace_name=manifest.target_workspace,
                    session_id=(
                        session_ids.get(old_session_id, old_session_id)
                        if old_session_id is not None
                        else None
                    ),
                    payload=rewritten_payload,
                    work_unit_key=rewritten_key,
                )
            )


async def _move_webhooks(db: AsyncSession, manifest: FamManifest) -> None:
    rows = await _mappings(
        db,
        "SELECT id,url,workspace_name FROM webhook_endpoints WHERE workspace_name = ANY(:sources)",
        sources=list(manifest.source_names),
    )
    for row in rows:
        existing = await _scalar(
            db,
            "SELECT id FROM webhook_endpoints WHERE workspace_name=:target AND url=:url",
            target=manifest.target_workspace,
            url=row["url"],
        )
        if existing:
            await db.execute(
                text("DELETE FROM webhook_endpoints WHERE id=:id"), {"id": row["id"]}
            )
        else:
            await db.execute(
                text(
                    "UPDATE webhook_endpoints SET workspace_name=:target WHERE id=:id"
                ),
                {"target": manifest.target_workspace, "id": row["id"]},
            )


async def _retire_sources_and_complete(db: AsyncSession, manifest: FamManifest) -> None:
    await db.execute(
        text(
            """
            DELETE FROM session_peers sp
            USING fam_session_map sm
            WHERE sp.workspace_name=sm.source_workspace
              AND sp.session_name=sm.source_session
            """
        )
    )
    await db.execute(
        text(
            """
            DELETE FROM sessions s
            USING fam_session_map sm
            WHERE s.workspace_name=sm.source_workspace
              AND s.name=sm.source_session
            """
        )
    )
    await db.execute(
        text(
            """
            DELETE FROM collections c
            WHERE c.workspace_name = ANY(:sources)
              AND NOT EXISTS (
                SELECT 1 FROM documents d
                WHERE d.workspace_name=c.workspace_name
                  AND d.observer=c.observer
                  AND d.observed=c.observed
              )
            """
        ),
        {"sources": list(manifest.source_names)},
    )

    for source in manifest.sources:
        source_metadata = {
            "famMigrationState": "archived",
            "famTargetWorkspace": manifest.target_workspace,
            "famArchivedSessions": sorted(source.leave_sessions),
        }
        source_configuration = {
            "reasoning": {"enabled": False},
            "summary": {"enabled": False},
            "dream": {"enabled": False},
        }
        await db.execute(
            update(models.Workspace)
            .where(models.Workspace.name == source.name)
            .values(
                h_metadata=models.Workspace.h_metadata.op("||")(source_metadata),
                configuration=models.Workspace.configuration.op("||")(
                    source_configuration
                ),
            )
        )

    await db.execute(
        update(models.Workspace)
        .where(models.Workspace.name == manifest.target_workspace)
        .values(
            h_metadata=models.Workspace.h_metadata.op("||")(
                {
                    "famMigrationState": "complete",
                    "famMigrationCompletedAt": datetime.now().astimezone().isoformat(),
                }
            ),
            internal_metadata=models.Workspace.internal_metadata.op("||")(
                {"fam_migration": {"state": "complete"}}
            ),
        )
    )


async def verify_migration(
    db: AsyncSession, manifest: FamManifest, plan: MigrationPlan | None = None
) -> dict[str, Any]:
    marker = await _scalar(
        db,
        "SELECT metadata->>'famMigrationState' FROM workspaces WHERE name=:target",
        target=manifest.target_workspace,
    )
    if marker != "complete":
        raise FamMigrationError("fam workspace completion marker is missing")

    peers = {
        str(row["name"])
        for row in await _mappings(
            db,
            "SELECT name FROM peers WHERE workspace_name=:target",
            target=manifest.target_workspace,
        )
    }
    if peers != set(CANONICAL_PEERS):
        raise FamMigrationError(f"fam peers are not canonical: {sorted(peers)}")

    noncanonical_messages = int(
        await _scalar(
            db,
            "SELECT count(*) FROM messages WHERE workspace_name=:target AND NOT (peer_name = ANY(:peers))",
            target=manifest.target_workspace,
            peers=list(CANONICAL_PEERS),
        )
        or 0
    )
    noncanonical_documents = int(
        await _scalar(
            db,
            """
            SELECT count(*) FROM documents
            WHERE workspace_name=:target
              AND (NOT (observer = ANY(:peers)) OR NOT (observed = ANY(:peers)))
            """,
            target=manifest.target_workspace,
            peers=list(CANONICAL_PEERS),
        )
        or 0
    )
    if noncanonical_messages or noncanonical_documents:
        raise FamMigrationError(
            "fam contains noncanonical identities: "
            f"messages={noncanonical_messages}, documents={noncanonical_documents}"
        )

    session_count = int(
        await _scalar(
            db,
            "SELECT count(*) FROM sessions WHERE workspace_name=:target",
            target=manifest.target_workspace,
        )
        or 0
    )
    if plan is not None and session_count != len(plan.sessions):
        raise FamMigrationError(
            f"fam session count mismatch: {session_count} != {len(plan.sessions)}"
        )

    pending = int(
        await _scalar(
            db,
            "SELECT count(*) FROM queue WHERE workspace_name=:target AND processed=false",
            target=manifest.target_workspace,
        )
        or 0
    )
    if pending:
        raise FamMigrationError(f"fam has {pending} pending queue rows after migration")

    return {
        "workspace": manifest.target_workspace,
        "peers": sorted(peers),
        "sessions": session_count,
        "messages": int(
            await _scalar(
                db,
                "SELECT count(*) FROM messages WHERE workspace_name=:target",
                target=manifest.target_workspace,
            )
            or 0
        ),
        "documents": int(
            await _scalar(
                db,
                "SELECT count(*) FROM documents WHERE workspace_name=:target",
                target=manifest.target_workspace,
            )
            or 0
        ),
    }


async def apply_migration(db: AsyncSession, manifest: FamManifest) -> MigrationResult:
    await _lock_tables(db)
    plan = await build_plan(db, manifest)
    await _install_temp_maps(db, manifest, plan)
    await _create_target_workspace_and_peers(db, manifest)
    await _create_target_sessions(db, manifest)
    await _create_target_session_peers(db, manifest)
    await _create_target_collections(db, manifest, plan)
    await _move_corpus(db, manifest)
    await _move_queue_history(db, manifest, plan)
    await _move_webhooks(db, manifest)
    await _retire_sources_and_complete(db, manifest)
    verification = await verify_migration(db, manifest, plan)
    return MigrationResult(
        target_workspace=manifest.target_workspace,
        report={**plan.report, "verification": verification},
    )
