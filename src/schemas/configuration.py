"""Configuration schemas for hierarchical settings resolution.

Covers workspace, session, and message-level configuration as well as
the fully-resolved variants used at runtime.
"""

import re
import unicodedata
from enum import Enum
from typing import Any, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config import settings
from src.utils.tokens import estimate_tokens

_CANONICAL_PEER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class PeerCanonicalCase(str, Enum):
    """Canonical case policies for workspace peer identities."""

    PRESERVE = "preserve"
    LOWER = "lower"


class DialecticRecallScope(str, Enum):
    """Evidence scopes available to the Dialectic recall path."""

    OBSERVER = "observer"
    WORKSPACE = "workspace"


class DreamType(str, Enum):
    """Types of dreams that can be triggered."""

    OMNI = "omni"


class ReasoningConfiguration(BaseModel):
    enabled: bool | None = Field(
        default=None,
        description="Whether to enable reasoning functionality.",
    )
    custom_instructions: str | None = Field(
        default=None,
        description="Optional custom instructions for the reasoning system on this workspace/session/message. Rejected if they exceed the deriver custom-instruction token cap.",
    )

    @field_validator("custom_instructions")
    @classmethod
    def validate_custom_instructions(cls, value: str | None) -> str | None:
        return _validate_custom_instructions_budget(value)


class DialecticConfiguration(BaseModel):
    """Read-time memory retrieval policy for the Dialectic agent."""

    recall_scope: DialecticRecallScope | None = Field(
        default=None,
        description=(
            "Whether Dialectic recall is limited to the requesting observer or "
            "federated across configured workspace observers."
        ),
    )
    recall_observers: list[str] | None = Field(
        default=None,
        description=(
            "Optional allowlist of observer peer IDs used when recall_scope is "
            "workspace. When omitted, all peers in the workspace are eligible."
        ),
    )

    @field_validator("recall_observers")
    @classmethod
    def validate_recall_observers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("recall_observers cannot be empty")
        normalized = [peer.strip() for peer in value]
        if any(not peer for peer in normalized):
            raise ValueError("recall_observers cannot contain blank peer IDs")
        if len(normalized) != len(set(normalized)):
            raise ValueError("recall_observers cannot contain duplicates")
        return normalized


def _normalize_identity_aliases(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None

    normalized: dict[str, str] = {}
    for presented_id, canonical_id in value.items():
        presented = unicodedata.normalize("NFKC", presented_id).strip()
        canonical = unicodedata.normalize("NFKC", canonical_id).strip()
        if not presented:
            raise ValueError("identity aliases cannot contain blank IDs")
        if not _CANONICAL_PEER_ID_PATTERN.fullmatch(canonical):
            raise ValueError(
                f"identity alias target {canonical_id!r} is not a canonical lowercase peer ID"
            )

        alias_key = presented.casefold()
        existing = normalized.get(alias_key)
        if existing is not None and existing != canonical:
            raise ValueError(
                f"identity aliases {presented_id!r} collide after case-folding"
            )
        normalized[alias_key] = canonical

    return normalized


class IdentityConfiguration(BaseModel):
    """Workspace-level peer identity registry and canonicalization policy."""

    canonical_case: PeerCanonicalCase | None = Field(
        default=None,
        description="How unknown dynamic peer IDs are canonicalized.",
    )
    strict: bool | None = Field(
        default=None,
        description="Whether presented IDs must resolve through the alias registry.",
    )
    allow_dynamic_peers: bool | None = Field(
        default=None,
        description="Whether identities absent from the registry may be created.",
    )
    aliases: dict[str, str] | None = Field(
        default=None,
        description=(
            "Presented peer IDs mapped to canonical lowercase peer IDs. Alias keys "
            "are normalized with Unicode NFKC and case-folding."
        ),
    )

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _normalize_identity_aliases(value)


class PeerCardConfiguration(BaseModel):
    use: bool | None = Field(
        default=None,
        description="Whether to use peer card related to this peer during reasoning process.",
    )
    create: bool | None = Field(
        default=None,
        description="Whether to generate peer card based on content.",
    )


class SummaryConfiguration(BaseModel):
    enabled: bool | None = Field(
        default=None,
        description="Whether to enable summary functionality.",
    )
    messages_per_short_summary: int | None = Field(
        default=None,
        ge=10,
        description="Number of messages per short summary. Must be positive, greater than or equal to 10, and less than messages_per_long_summary.",
    )
    messages_per_long_summary: int | None = Field(
        default=None,
        ge=20,
        description="Number of messages per long summary. Must be positive, greater than or equal to 20, and greater than messages_per_short_summary.",
    )

    @model_validator(mode="after")
    def validate_summary_thresholds(self) -> Self:
        """Validate that short summary threshold <= long summary threshold."""
        short = self.messages_per_short_summary
        long = self.messages_per_long_summary

        if short is not None and long is not None and short >= long:
            raise ValueError(
                "messages_per_short_summary must be less than messages_per_long_summary"
            )

        return self


class DreamConfiguration(BaseModel):
    enabled: bool | None = Field(
        default=None,
        description="Whether to enable dream functionality. If reasoning is disabled, dreams will also be disabled and this setting will be ignored.",
    )


def _validate_custom_instructions_budget(
    custom_instructions: str | None,
) -> str | None:
    if custom_instructions is None:
        return None
    if not custom_instructions.strip():
        return custom_instructions

    max_tokens = settings.DERIVER.MAX_CUSTOM_INSTRUCTIONS_TOKENS
    if max_tokens <= 0:
        raise ValueError("custom_instructions are not enabled for this deployment")

    if estimate_tokens(custom_instructions) > max_tokens:
        raise ValueError(
            f"custom_instructions exceeds DERIVER.MAX_CUSTOM_INSTRUCTIONS_TOKENS ({max_tokens} tokens)"
        )

    return custom_instructions


class WorkspaceConfiguration(BaseModel):
    """
    The set of options that can be in a workspace DB-level configuration dictionary.

    All fields are optional. Session-level configuration overrides workspace-level configuration, which overrides global configuration.
    """

    model_config = ConfigDict(extra="allow")  # pyright: ignore

    reasoning: ReasoningConfiguration | None = Field(
        default=None,
        description="Configuration for reasoning functionality.",
    )
    dialectic: DialecticConfiguration | None = Field(
        default=None,
        description="Configuration for Dialectic read-time recall.",
    )
    identity: IdentityConfiguration | None = Field(
        default=None,
        description="Workspace peer identity registry and canonicalization policy.",
    )
    peer_card: PeerCardConfiguration | None = Field(
        default=None,
        description="Configuration for peer card functionality. If reasoning is disabled, peer cards will also be disabled and these settings will be ignored.",
    )
    summary: SummaryConfiguration | None = Field(
        default=None,
        description="Configuration for summary functionality.",
    )
    dream: DreamConfiguration | None = Field(
        default=None,
        description="Configuration for dream functionality. If reasoning is disabled, dreams will also be disabled and these settings will be ignored.",
    )


class SessionConfiguration(WorkspaceConfiguration):
    """
    The set of options that can be in a session DB-level configuration dictionary.

    All fields are optional. Session-level configuration overrides workspace-level configuration, which overrides global configuration.
    """

    pass


class MessageConfiguration(BaseModel):
    """
    The set of options that can be in a message DB-level configuration dictionary.

    All fields are optional. Message-level configuration overrides all other configurations.
    """

    reasoning: ReasoningConfiguration | None = Field(
        default=None,
        description="Configuration for reasoning functionality.",
    )


class ResolvedReasoningConfiguration(BaseModel):
    enabled: bool
    custom_instructions: str | None = None

    @field_validator("custom_instructions")
    @classmethod
    def validate_custom_instructions(cls, value: str | None) -> str | None:
        return _validate_custom_instructions_budget(value)


class ResolvedDialecticConfiguration(BaseModel):
    recall_scope: DialecticRecallScope = DialecticRecallScope.OBSERVER
    recall_observers: list[str] | None = None


class ResolvedIdentityConfiguration(BaseModel):
    canonical_case: PeerCanonicalCase = PeerCanonicalCase.PRESERVE
    strict: bool = False
    allow_dynamic_peers: bool = True
    aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        return _normalize_identity_aliases(value) or {}


class ResolvedPeerCardConfiguration(BaseModel):
    use: bool
    create: bool


class ResolvedSummaryConfiguration(BaseModel):
    enabled: bool
    messages_per_short_summary: int
    messages_per_long_summary: int


class ResolvedDreamConfiguration(BaseModel):
    enabled: bool


class ResolvedConfiguration(BaseModel):
    """
    The final resolved configuration for a given message.
    Hierarchy: message > session > workspace > global configuration
    """

    reasoning: ResolvedReasoningConfiguration
    dialectic: ResolvedDialecticConfiguration = Field(
        default_factory=ResolvedDialecticConfiguration
    )
    identity: ResolvedIdentityConfiguration = Field(
        default_factory=ResolvedIdentityConfiguration
    )
    peer_card: ResolvedPeerCardConfiguration
    summary: ResolvedSummaryConfiguration
    dream: ResolvedDreamConfiguration

    @model_validator(mode="before")
    @classmethod
    def migrate_deriver_to_reasoning(cls, data: Any) -> Any:
        """Handle v3.0.0 migration: 'deriver' was renamed to 'reasoning'."""
        if not isinstance(data, dict):
            return data

        config = cast(dict[str, Any], data)

        if "deriver" in config and "reasoning" not in config:
            config["reasoning"] = config.pop("deriver")

        return config


class PeerConfig(BaseModel):
    # TODO: Update description - should say "Whether honcho forms a representation of the peer itself"
    observe_me: bool | None = Field(
        default=None,
        description="Whether Honcho will use reasoning to form a representation of this peer",
    )


class SessionPeerConfig(PeerConfig):
    # TODO: Update description - should say "Whether this peer forms representations of other peers in the session"
    observe_others: bool | None = Field(
        default=None,
        description="Whether this peer should form a session-level theory-of-mind representation of other peers in the session",
    )
