from typing import Any

import pytest

from src import models
from src.config import settings
from src.schemas import MessageConfiguration, ReasoningConfiguration
from src.utils.config_helpers import get_configuration


def _workspace(configuration: dict[str, Any]) -> models.Workspace:
    return models.Workspace(name="workspace", configuration=configuration)


def _session(configuration: dict[str, Any]) -> models.Session:
    return models.Session(
        name="session",
        workspace_name="workspace",
        configuration=configuration,
    )


def test_preserves_workspace_custom_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.DERIVER, "MAX_CUSTOM_INSTRUCTIONS_TOKENS", 100)

    workspace = _workspace(
        {
            "reasoning": {
                "custom_instructions": "Use the workspace-specific guidance.",
            }
        }
    )

    configuration = get_configuration(None, None, workspace)

    assert (
        configuration.reasoning.custom_instructions
        == "Use the workspace-specific guidance."
    )


def test_message_custom_instructions_override_session_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.DERIVER, "MAX_CUSTOM_INSTRUCTIONS_TOKENS", 100)

    workspace = _workspace(
        {
            "reasoning": {
                "custom_instructions": "Use the workspace-specific guidance.",
            }
        }
    )
    session = _session(
        {
            "reasoning": {
                "custom_instructions": "Use the session-specific guidance.",
            }
        }
    )
    message = MessageConfiguration(
        reasoning=ReasoningConfiguration(
            custom_instructions="Use the message-specific guidance.",
        ),
    )

    configuration = get_configuration(message, session, workspace)

    assert (
        configuration.reasoning.custom_instructions
        == "Use the message-specific guidance."
    )


def test_dialectic_recall_defaults_to_observer_scope() -> None:
    configuration = get_configuration(None, None, _workspace({}))

    assert configuration.dialectic.recall_scope == "observer"
    assert configuration.dialectic.recall_observers is None


def test_workspace_dialectic_recall_configuration_is_resolved() -> None:
    workspace = _workspace(
        {
            "dialectic": {
                "recall_scope": "workspace",
                "recall_observers": ["sarah", "sky", "luke", "kate"],
            }
        }
    )

    configuration = get_configuration(None, None, workspace)

    assert configuration.dialectic.recall_scope == "workspace"
    assert configuration.dialectic.recall_observers == [
        "sarah",
        "sky",
        "luke",
        "kate",
    ]


def test_identity_defaults_preserve_existing_workspace_semantics() -> None:
    configuration = get_configuration(None, None, _workspace({}))

    assert configuration.identity.canonical_case == "preserve"
    assert configuration.identity.strict is False
    assert configuration.identity.allow_dynamic_peers is True
    assert configuration.identity.aliases == {}


def test_workspace_identity_configuration_is_resolved() -> None:
    workspace = _workspace(
        {
            "identity": {
                "canonical_case": "lower",
                "strict": True,
                "allow_dynamic_peers": False,
                "aliases": {"Bex": "bex", "Codex": "kate"},
            }
        }
    )

    configuration = get_configuration(None, None, workspace)

    assert configuration.identity.canonical_case == "lower"
    assert configuration.identity.strict is True
    assert configuration.identity.allow_dynamic_peers is False
    assert configuration.identity.aliases == {"bex": "bex", "codex": "kate"}
