from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs

import httpx
import pytest

from src.exceptions import AuthenticationException, ValidationException
from src.llm.codex_auth import CODEX_OAUTH_CLIENT_ID, CodexAuthManager
from tests.codex_test_helpers import (
    codex_auth_payload,
    codex_jwt,
    read_codex_auth,
    write_codex_auth,
)


@pytest.mark.asyncio
async def test_loads_valid_codex_auth_without_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    write_codex_auth(auth_file, codex_auth_payload())

    manager = CodexAuthManager(auth_file=auth_file)
    tokens = await manager.get_tokens()

    assert tokens.access_token
    assert tokens.refresh_token == "fake-refresh-token"
    assert tokens.account_id == "fake-account-id"
    assert tokens.id_token == "fake-id-token"


@pytest.mark.asyncio
async def test_missing_codex_auth_file_raises_sanitized_error(tmp_path: Path) -> None:
    manager = CodexAuthManager(auth_file=tmp_path / "missing-auth.json")

    with pytest.raises(AuthenticationException) as exc_info:
        await manager.get_tokens()

    message = str(exc_info.value)
    assert "missing-auth.json" in message
    assert "fake-refresh-token" not in message


@pytest.mark.asyncio
async def test_malformed_codex_auth_file_raises_validation_error(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{not json")

    manager = CodexAuthManager(auth_file=auth_file)

    with pytest.raises(ValidationException, match="not valid JSON"):
        await manager.get_tokens()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("access_token", ""),
        ("access_token", None),
        ("refresh_token", ""),
        ("refresh_token", None),
        ("account_id", ""),
        ("account_id", None),
    ],
)
@pytest.mark.asyncio
async def test_auth_file_missing_required_token_fields_raises_validation_error(
    tmp_path: Path,
    field: str,
    value: str | None,
) -> None:
    auth_file = tmp_path / "auth.json"
    payload = codex_auth_payload()
    tokens = payload["tokens"]
    assert isinstance(tokens, dict)
    tokens = cast(dict[str, Any], tokens)
    if value is None:
        tokens.pop(field)
    else:
        tokens[field] = value
    write_codex_auth(auth_file, payload)

    manager = CodexAuthManager(auth_file=auth_file)

    with pytest.raises(ValidationException, match=field):
        await manager.get_tokens()


@pytest.mark.asyncio
async def test_refreshes_expired_token_and_writes_mode_600(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    write_codex_auth(
        auth_file,
        codex_auth_payload(access_token=codex_jwt(time.time() - 60)),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": codex_jwt(time.time() + 3600),
                "refresh_token": "new-fake-refresh-token",
                "id_token": "new-fake-id-token",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manager = CodexAuthManager(auth_file=auth_file, http_client=client)
        tokens = await manager.get_tokens()

    assert tokens.refresh_token == "new-fake-refresh-token"
    assert tokens.id_token == "new-fake-id-token"
    assert requests
    form = parse_qs(requests[0].content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["fake-refresh-token"]
    assert form["client_id"] == [CODEX_OAUTH_CLIENT_ID]
    assert auth_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_refresh_rejects_missing_access_token_without_writing(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    original = codex_auth_payload(access_token=codex_jwt(time.time() - 60))
    write_codex_auth(auth_file, original)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"refresh_token": "new-refresh-token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manager = CodexAuthManager(auth_file=auth_file, http_client=client)
        with pytest.raises(AuthenticationException, match="missing access_token"):
            await manager.get_tokens()

    assert read_codex_auth(auth_file) == original


@pytest.mark.asyncio
async def test_refresh_rejects_expired_access_token_without_writing(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    original = codex_auth_payload(access_token=codex_jwt(time.time() - 60))
    write_codex_auth(auth_file, original)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": codex_jwt(time.time() - 30),
                "refresh_token": "new-refresh-token",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manager = CodexAuthManager(auth_file=auth_file, http_client=client)
        with pytest.raises(AuthenticationException, match="expired access token"):
            await manager.get_tokens()

    assert read_codex_auth(auth_file) == original


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    write_codex_auth(
        auth_file,
        codex_auth_payload(access_token=codex_jwt(time.time() - 60)),
    )
    refresh_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal refresh_count
        refresh_count += 1
        return httpx.Response(
            200,
            json={
                "access_token": codex_jwt(time.time() + 3600),
                "refresh_token": "fresh-refresh-token",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manager = CodexAuthManager(auth_file=auth_file, http_client=client)
        first, second = await asyncio.gather(
            manager.get_tokens(),
            manager.get_tokens(),
        )

    assert first.refresh_token == "fresh-refresh-token"
    assert second.refresh_token == "fresh-refresh-token"
    assert refresh_count == 1


@pytest.mark.asyncio
async def test_concurrent_managers_share_one_refresh(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    write_codex_auth(
        auth_file,
        codex_auth_payload(access_token=codex_jwt(time.time() - 60)),
    )
    refresh_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal refresh_count
        refresh_count += 1
        return httpx.Response(
            200,
            json={
                "access_token": codex_jwt(time.time() + 3600),
                "refresh_token": "fresh-refresh-token",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first_manager = CodexAuthManager(auth_file=auth_file, http_client=client)
        second_manager = CodexAuthManager(auth_file=auth_file, http_client=client)
        first, second = await asyncio.gather(
            first_manager.get_tokens(),
            second_manager.get_tokens(),
        )

    assert first.refresh_token == "fresh-refresh-token"
    assert second.refresh_token == "fresh-refresh-token"
    assert refresh_count == 1
