"""Collective memory retrieval for the Dialectic read path."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src import models
from src.utils.formatting import parse_datetime_iso


def _normalize_content(content: str) -> str:
    """Normalize content for conservative exact-equivalence deduplication."""
    return " ".join(unicodedata.normalize("NFKC", content).casefold().split())


def _document_timestamp(document: models.Document) -> datetime:
    value = document.internal_metadata.get("message_created_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return parse_datetime_iso(value)
        except ValueError:
            pass
    return document.created_at


@dataclass(frozen=True)
class MemoryEvidence:
    """One ranked memory item with provenance from equivalent observations."""

    document_id: str
    document_ids: tuple[str, ...]
    content: str
    level: str
    observed: str
    observers: tuple[str, ...]
    created_at: datetime
    session_name: str | None
    source_ids: tuple[str, ...]
    internal_metadata: dict[str, Any]

    @classmethod
    def from_document(cls, document: models.Document) -> MemoryEvidence:
        return cls(
            document_id=document.id,
            document_ids=(document.id,),
            content=document.content,
            level=document.level,
            observed=document.observed,
            observers=(document.observer,),
            created_at=_document_timestamp(document),
            session_name=document.session_name,
            source_ids=tuple(document.source_ids or ()),
            internal_metadata=dict(document.internal_metadata),
        )

    def merge(self, document: models.Document) -> MemoryEvidence:
        """Merge an exact-equivalent observation without changing rank."""
        observers = tuple(dict.fromkeys((*self.observers, document.observer)))
        document_ids = tuple(dict.fromkeys((*self.document_ids, document.id)))
        source_ids = tuple(
            dict.fromkeys((*self.source_ids, *(document.source_ids or ())))
        )
        return MemoryEvidence(
            document_id=self.document_id,
            document_ids=document_ids,
            content=self.content,
            level=self.level,
            observed=self.observed,
            observers=observers,
            created_at=max(self.created_at, _document_timestamp(document)),
            session_name=self.session_name,
            source_ids=source_ids,
            internal_metadata=self.internal_metadata,
        )

    def format_markdown(self, *, include_id: bool) -> str:
        known_by = ", ".join(self.observers)
        timestamp = self.created_at.replace(microsecond=0, tzinfo=None)
        id_prefix = f"[id:{self.document_id}] " if include_id else ""
        provenance = f"[known by: {known_by}]"

        if self.level == "explicit":
            return f"{provenance} [{timestamp}] {self.content}"

        if self.level == "deductive":
            lines = [f"{id_prefix}{provenance} [{timestamp}] {self.content}"]
            premises = self.internal_metadata.get("premises", [])
            if premises:
                lines.append("   Premises:")
                lines.extend(f"   - {premise}" for premise in premises)
            return "\n".join(lines)

        if self.level == "inductive":
            confidence = self.internal_metadata.get("confidence", "medium")
            pattern_type = self.internal_metadata.get("pattern_type", "pattern")
            lines = [
                f"{id_prefix}{provenance} **Pattern** [{confidence}]: {self.content}",
                f"   **Type**: {pattern_type}",
            ]
            sources = self.internal_metadata.get("sources", [])
            if sources:
                lines.append("   **Sources**:")
                lines.extend(f"   - {source}" for source in sources[:5])
                if len(sources) > 5:
                    lines.append(f"   - ... and {len(sources) - 5} more")
            return "\n".join(lines)

        if self.level == "contradiction":
            lines = [f"{id_prefix}{provenance} **CONTRADICTION**: {self.content}"]
            sources = self.internal_metadata.get("sources", [])
            if sources:
                lines.append("   **Conflicting statements**:")
                lines.extend(f"   - {source}" for source in sources)
            return "\n".join(lines)

        return f"{id_prefix}{provenance} [{self.level}] {self.content}"


@dataclass(frozen=True)
class MemoryEvidenceSet:
    """Globally ranked, conservatively deduplicated evidence."""

    items: tuple[MemoryEvidence, ...]

    @classmethod
    def from_documents(
        cls, documents: Sequence[models.Document], *, limit: int
    ) -> MemoryEvidenceSet:
        ordered_keys: list[tuple[str, str, str]] = []
        evidence_by_key: dict[tuple[str, str, str], MemoryEvidence] = {}

        for document in documents:
            key = (
                document.observed,
                document.level,
                _normalize_content(document.content),
            )
            existing = evidence_by_key.get(key)
            if existing is None:
                ordered_keys.append(key)
                evidence_by_key[key] = MemoryEvidence.from_document(document)
            else:
                evidence_by_key[key] = existing.merge(document)

        items = tuple(evidence_by_key[key] for key in ordered_keys[:limit])
        return cls(items=items)

    def is_empty(self) -> bool:
        return not self.items

    def __len__(self) -> int:
        return len(self.items)

    def format_as_markdown(self, *, include_ids: bool) -> str:
        sections: list[str] = []
        headings = {
            "explicit": "## Explicit Observations",
            "deductive": "## Deductive Observations",
            "inductive": "## Inductive Observations",
            "contradiction": "## Contradictions",
        }
        for level in ("explicit", "deductive", "inductive", "contradiction"):
            level_items = [item for item in self.items if item.level == level]
            if not level_items:
                continue
            sections.append(headings[level])
            sections.extend(
                item.format_markdown(include_id=include_ids and level != "explicit")
                for item in level_items
            )
        return "\n\n".join(sections)


async def retrieve_memory_evidence(
    *,
    workspace_name: str,
    observers: Sequence[str],
    observed: str,
    query: str,
    limit: int,
    levels: list[str] | None = None,
    embedding: list[float] | None = None,
) -> MemoryEvidenceSet:
    """Retrieve one globally ranked evidence pool across observer collections."""
    # Import after module initialization to avoid the CRUD -> dreamer ->
    # agent_tools -> retrieval startup cycle.
    from src import crud

    filters: dict[str, Any] | None = None
    if levels:
        filters = {"level": {"in": levels}}

    candidate_limit = max(limit * 3, limit)
    documents = await crud.query_documents_for_observers(
        db=None,
        workspace_name=workspace_name,
        observers=observers,
        observed=observed,
        query=query,
        top_k=candidate_limit,
        filters=filters,
        embedding=embedding,
    )
    return MemoryEvidenceSet.from_documents(documents, limit=limit)
