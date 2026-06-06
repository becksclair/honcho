from __future__ import annotations

import asyncio
import fcntl
import json
import os
import tempfile
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, cast

import httpx
import jwt

from src.exceptions import AuthenticationException, ValidationException

CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_AUTH_FILE_LOCKS: dict[Path, asyncio.Lock] = {}
_AUTH_FILE_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class CodexAuthTokens:
    access_token: str
    refresh_token: str
    account_id: str
    id_token: str | None = None


class CodexAuthManager:
    """Load and refresh ChatGPT Codex OAuth auth JSON.

    The auth file uses the same shape as Codex's normal `auth.json`. Token
    values must never be printed or included in exception messages.
    """

    auth_file: Path
    refresh_skew_seconds: int
    token_url: str
    _http_client: httpx.AsyncClient | None
    _lock: asyncio.Lock

    def __init__(
        self,
        *,
        auth_file: str | Path,
        refresh_skew_seconds: int = 300,
        token_url: str = CODEX_OAUTH_TOKEN_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.auth_file = Path(auth_file).expanduser()
        self.refresh_skew_seconds = refresh_skew_seconds
        self.token_url = token_url
        self._http_client = http_client
        self._lock = _lock_for_auth_file(self.auth_file)

    async def get_tokens(self) -> CodexAuthTokens:
        async with self._lock, self._process_file_lock():
            data = self._read_auth_file()
            tokens = self._extract_tokens(data)
            if not self._needs_refresh(tokens.access_token):
                return tokens
            return await self._refresh_and_write_tokens(data, tokens.refresh_token)

    async def force_refresh(self) -> CodexAuthTokens:
        async with self._lock, self._process_file_lock():
            data = self._read_auth_file()
            tokens = self._extract_tokens(data)
            return await self._refresh_and_write_tokens(data, tokens.refresh_token)

    @asynccontextmanager
    async def _process_file_lock(
        self,
    ) -> AsyncGenerator[None]:
        lock_file = await asyncio.to_thread(self._open_process_lock_file)
        locked = False
        try:
            await asyncio.to_thread(fcntl.flock, lock_file.fileno(), fcntl.LOCK_EX)
            locked = True
            yield
        except OSError as exc:
            raise AuthenticationException(
                f"Could not lock Codex auth file: {self.auth_file}"
            ) from exc
        finally:
            try:
                if locked:
                    await asyncio.to_thread(
                        fcntl.flock,
                        lock_file.fileno(),
                        fcntl.LOCK_UN,
                    )
            finally:
                await asyncio.to_thread(lock_file.close)

    def _open_process_lock_file(self) -> TextIO:
        lock_path = self.auth_file.with_name(f".{self.auth_file.name}.lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise AuthenticationException(
                f"Could not lock Codex auth file: {self.auth_file}"
            ) from exc
        return os.fdopen(fd, "a+")

    def _read_auth_file(self) -> dict[str, Any]:
        try:
            raw = self.auth_file.read_text()
        except FileNotFoundError as exc:
            raise AuthenticationException(
                f"Codex auth file not found: {self.auth_file}"
            ) from exc
        except OSError as exc:
            raise AuthenticationException(
                f"Could not read Codex auth file: {self.auth_file}"
            ) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationException(
                f"Codex auth file is not valid JSON: {self.auth_file}"
            ) from exc
        if not isinstance(data, dict):
            raise ValidationException("Codex auth file must contain a JSON object")
        return cast(dict[str, Any], data)

    @staticmethod
    def _extract_tokens(data: dict[str, Any]) -> CodexAuthTokens:
        raw_tokens = data.get("tokens")
        if not isinstance(raw_tokens, dict):
            raise ValidationException("Codex auth file is missing tokens")
        tokens = cast(dict[str, Any], raw_tokens)

        missing = [
            key
            for key in ("access_token", "refresh_token", "account_id")
            if not isinstance(tokens.get(key), str) or not tokens.get(key)
        ]
        if missing:
            raise ValidationException(
                "Codex auth file is missing required token fields: "
                + ", ".join(missing)
            )

        id_token = tokens.get("id_token")
        return CodexAuthTokens(
            access_token=cast(str, tokens["access_token"]),
            refresh_token=cast(str, tokens["refresh_token"]),
            account_id=cast(str, tokens["account_id"]),
            id_token=id_token if isinstance(id_token, str) else None,
        )

    def _needs_refresh(self, access_token: str) -> bool:
        try:
            decoded = jwt.decode(
                access_token,
                options={"verify_signature": False, "verify_aud": False},
                algorithms=["RS256", "HS256"],
            )
        except jwt.PyJWTError:
            return True
        exp = decoded.get("exp")
        if not isinstance(exp, int | float):
            return True
        return exp <= time.time() + self.refresh_skew_seconds

    async def _refresh(self, refresh_token: str) -> dict[str, Any]:
        client = self._http_client or httpx.AsyncClient(timeout=60.0)
        close_client = self._http_client is None
        try:
            response = await client.post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": CODEX_OAUTH_CLIENT_ID,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise AuthenticationException("Codex auth refresh request failed") from exc
        finally:
            if close_client:
                await client.aclose()

        if response.status_code >= 400:
            raise AuthenticationException(
                f"Codex auth refresh failed with HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise AuthenticationException(
                "Codex auth refresh returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise AuthenticationException("Codex auth refresh returned invalid payload")
        return cast(dict[str, Any], payload)

    async def _refresh_and_write_tokens(
        self,
        data: dict[str, Any],
        refresh_token: str,
    ) -> CodexAuthTokens:
        refreshed = await self._refresh(refresh_token)
        refreshed_access_token = self._required_refreshed_access_token(refreshed)
        if self._needs_refresh(refreshed_access_token):
            raise AuthenticationException(
                "Codex auth refresh returned an expired access token"
            )
        merged = self._merge_refreshed_tokens(data, refreshed)
        tokens = self._extract_tokens(merged)
        self._write_auth_file(merged)
        return tokens

    @staticmethod
    def _required_refreshed_access_token(refreshed: dict[str, Any]) -> str:
        access_token = refreshed.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise AuthenticationException(
                "Codex auth refresh response is missing access_token"
            )
        return access_token

    @staticmethod
    def _merge_refreshed_tokens(
        data: dict[str, Any],
        refreshed: dict[str, Any],
    ) -> dict[str, Any]:
        raw_tokens = data.get("tokens")
        tokens = (
            dict(cast(dict[str, Any], raw_tokens))
            if isinstance(raw_tokens, dict)
            else {}
        )
        for key in ("access_token", "refresh_token", "id_token", "account_id"):
            value = refreshed.get(key)
            if isinstance(value, str) and value:
                tokens[key] = value
        merged = dict(data)
        merged["tokens"] = tokens
        merged["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return merged

    def _write_auth_file(self, data: dict[str, Any]) -> None:
        parent = self.auth_file.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self.auth_file.name}.",
                suffix=".tmp",
                dir=parent,
                text=True,
            )
            with os.fdopen(fd, "w") as tmp:
                json.dump(data, tmp, separators=(",", ":"))
                tmp.write("\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.auth_file)
            os.chmod(self.auth_file, 0o600)
        except OSError as exc:
            raise AuthenticationException(
                f"Could not write Codex auth file: {self.auth_file}"
            ) from exc


def _lock_for_auth_file(auth_file: Path) -> asyncio.Lock:
    key = auth_file.resolve(strict=False)
    with _AUTH_FILE_LOCKS_GUARD:
        lock = _AUTH_FILE_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _AUTH_FILE_LOCKS[key] = lock
        return lock


__all__ = [
    "CODEX_OAUTH_CLIENT_ID",
    "CODEX_OAUTH_TOKEN_URL",
    "CodexAuthManager",
    "CodexAuthTokens",
]
