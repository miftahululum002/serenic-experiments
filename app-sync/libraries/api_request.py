from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import requests


class ApiError(RuntimeError):
    """Raised when the remote API returns an unsuccessful response."""


@dataclass(frozen=True)
class Organization:
    id: str
    name: str
    email: str
    password: str
    api_url: str


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None

    def is_expired(self, leeway_seconds: int = 30) -> bool:
        return self.expires_at is not None and datetime.now(timezone.utc).timestamp() >= self.expires_at - leeway_seconds


def _url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _response_details(response: requests.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text.strip()
    return f"HTTP {response.status_code}: {body}"


def _get_path(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def extract_token(body: Any, token_path: str | None = None) -> str:
    """Extract a bearer token from common login response shapes."""
    if token_path:
        token = _get_path(body, token_path)
        if isinstance(token, str) and token:
            return token.removeprefix("Bearer ")
        raise ApiError(f"Token tidak ditemukan pada path respons: {token_path}")

    candidates = (
        "accessToken",
        "access_token",
        "token",
        "data.accessToken",
        "data.access_token",
        "data.token",
        "result.accessToken",
        "result.access_token",
        "result.token",
    )
    for candidate in candidates:
        token = _get_path(body, candidate)
        if isinstance(token, str) and token:
            return token.removeprefix("Bearer ")
    raise ApiError(
        "Token tidak ditemukan pada respons login. "
        "Gunakan opsi --token-path sesuai struktur respons API."
    )


def extract_expiry(body: Any, token: str) -> float | None:
    """Read JWT exp when available, otherwise use common expiresIn fields."""
    parts = token.split(".")
    if len(parts) == 3:
        try:
            encoded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(encoded))
            if isinstance(claims.get("exp"), (int, float)):
                return float(claims["exp"])
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
            pass
    for path in ("expiresAt", "data.expiresAt", "expiresIn", "data.expiresIn"):
        value = _get_path(body, path)
        if isinstance(value, (int, float)):
            return datetime.now(timezone.utc).timestamp() + value if "In" in path else float(value)
    return None


class SyncClient:
    def __init__(
        self,
        organization: Organization,
        login_path: str,
        sync_path: str,
        refresh_path: str = "/app/v1/api/auth/refresh-token",
        timeout: float = 30.0,
        verify_tls: bool = True,
        token_path: str | None = None,
        refresh_token_path: str | None = "data.token",
        refresh_payload_key: str = "refreshToken",
        refresh_payload: dict[str, Any] | None = None,
        login_extra: dict[str, Any] | None = None,
    ) -> None:
        self.organization = organization
        self.login_path = login_path
        self.sync_path = sync_path
        self.refresh_path = refresh_path
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.token_path = token_path
        self.refresh_token_path = refresh_token_path
        self.refresh_payload_key = refresh_payload_key
        self.refresh_payload = refresh_payload or {}
        self.login_extra = login_extra or {}
        self.current_tokens: TokenSet | None = None
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def login(self) -> TokenSet:
        payload = {
            "email": self.organization.email,
            "password": self.organization.password,
            **self.login_extra,
        }
        response = self.session.post(
            _url(self.organization.api_url, self.login_path),
            json=payload,
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        if not response.ok:
            raise ApiError(f"Login organisasi {self.organization.name!r} gagal: {_response_details(response)}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ApiError("Respons login bukan JSON yang valid") from exc
        access_token = extract_token(body, self.token_path)
        refresh_token = None
        if self.refresh_token_path:
            refresh_token = _get_path(body, self.refresh_token_path)
            if not isinstance(refresh_token, str) or not refresh_token:
                refresh_token = None
        self.current_tokens = TokenSet(access_token, refresh_token, extract_expiry(body, access_token))
        return self.current_tokens

    def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise ApiError("Refresh token tidak tersedia pada respons login")
        response = self.session.post(
            _url(self.organization.api_url, self.refresh_path),
            json={**self.refresh_payload, self.refresh_payload_key: tokens.refresh_token},
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        if not response.ok:
            raise ApiError(
                f"Refresh token organisasi {self.organization.name!r} gagal: {_response_details(response)}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ApiError("Respons refresh token bukan JSON yang valid") from exc
        access_token = extract_token(body, self.token_path)
        refreshed_token = _get_path(body, self.refresh_token_path) if self.refresh_token_path else None
        if not isinstance(refreshed_token, str) or not refreshed_token:
            refreshed_token = tokens.refresh_token
        self.current_tokens = TokenSet(access_token, refreshed_token, extract_expiry(body, access_token))
        return self.current_tokens

    def synchronize(self, tokens: TokenSet, payload: dict[str, Any]) -> Any:
        if tokens.is_expired():
            tokens = self.refresh(tokens)
        self.current_tokens = tokens
        response = self.session.post(
            _url(self.organization.api_url, self.sync_path),
            json=payload,
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        if response.status_code == 401:
            tokens = self.refresh(tokens)
            self.current_tokens = tokens
            response = self.session.post(
                _url(self.organization.api_url, self.sync_path),
                json=payload,
                headers={"Authorization": f"Bearer {tokens.access_token}"},
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        if not response.ok:
            raise ApiError(
                f"Sinkronisasi organisasi {self.organization.name!r} gagal: "
                f"{_response_details(response)}"
            )
        try:
            return response.json()
        except ValueError:
            return {"status_code": response.status_code, "text": response.text}
