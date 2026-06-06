from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, cast

import jwt

CODEX_TEST_JWT_SECRET = "test-secret-with-at-least-32-bytes"


def codex_jwt(exp: int | float | None = None) -> str:
    return jwt.encode(
        {"exp": time.time() + 3600 if exp is None else exp},
        CODEX_TEST_JWT_SECRET,
        algorithm="HS256",
    )


def codex_auth_payload(
    *,
    access_token: str | None = None,
    refresh_token: str = "fake-refresh-token",
    account_id: str = "fake-account-id",
    id_token: str | None = "fake-id-token",
) -> dict[str, Any]:
    tokens: dict[str, Any] = {
        "access_token": codex_jwt() if access_token is None else access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
    }
    if id_token is not None:
        tokens["id_token"] = id_token
    return {
        "auth_mode": "chatgpt",
        "tokens": tokens,
    }


def write_codex_auth(
    path: Path,
    payload: dict[str, Any] | None = None,
    *,
    access_token: str | None = None,
    account_id: str = "fake-account-id",
    id_token: str | None = "fake-id-token",
) -> None:
    path.write_text(
        json.dumps(
            payload
            if payload is not None
            else codex_auth_payload(
                access_token=access_token,
                account_id=account_id,
                id_token=id_token,
            )
        )
    )
    os.chmod(path, 0o600)


def read_codex_auth(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    assert isinstance(data, dict)
    return cast(dict[str, Any], data)
