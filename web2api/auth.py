"""Authentication helpers for protected Web2API HTTP surfaces."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ACCESS_TOKEN_ENV = "WEB2API_ACCESS_TOKEN"
ACCESS_TOKEN_FILE_ENV = "WEB2API_ACCESS_TOKEN_FILE"
AUTH_HEADER = "Authorization"
ALT_AUTH_HEADER = "X-Web2API-Key"
AUTH_STORAGE_KEY = "web2api.access_token"


@dataclass(slots=True)
class AuthConfig:
    """Runtime authentication configuration for protected routes."""

    access_token: str | None = None

    @property
    def enabled(self) -> bool:
        return self.access_token is not None

    def requires_auth(self, path: str) -> bool:
        """Return ``True`` when *path* is protected by access-token auth."""
        if not self.enabled:
            return False
        return (
            path == "/api/recipes/manage"
            or path.startswith("/api/recipes/manage/")
            or path == "/mcp"
            or path.startswith("/mcp/")
        )


def _read_secret_file(path_value: str) -> str | None:
    path = Path(path_value).expanduser()
    if not path.exists() or not path.is_file():
        raise ValueError(f"{ACCESS_TOKEN_FILE_ENV} does not point to a readable file: {path}")
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def load_auth_config() -> AuthConfig:
    """Load auth configuration from environment variables."""
    file_value = os.environ.get(ACCESS_TOKEN_FILE_ENV)
    token_value: str | None = None
    if file_value is not None and file_value.strip():
        token_value = _read_secret_file(file_value.strip())
    elif ACCESS_TOKEN_ENV in os.environ:
        raw_value = os.environ.get(ACCESS_TOKEN_ENV)
        if raw_value is not None:
            token_value = raw_value.strip() or None
    return AuthConfig(access_token=token_value)


def _extract_bearer_token(header_value: str | None) -> str | None:
    if header_value is None:
        return None
    scheme, _, value = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    normalized = value.strip()
    return normalized or None


def request_is_authorized(headers: Any, config: AuthConfig) -> bool:
    """Return ``True`` when request headers satisfy configured auth."""
    if not config.enabled or config.access_token is None:
        return True

    provided = _extract_bearer_token(headers.get("authorization"))
    if provided is None:
        alternate = headers.get(ALT_AUTH_HEADER)
        if isinstance(alternate, str):
            provided = alternate.strip() or None

    if provided is None:
        return False
    return secrets.compare_digest(provided, config.access_token)


def public_auth_payload(config: AuthConfig) -> dict[str, Any]:
    """Serialize non-secret auth state for the index UI."""
    return {
        "enabled": config.enabled,
        "header": AUTH_HEADER,
        "scheme": "Bearer",
        "alternate_header": ALT_AUTH_HEADER,
        "storage_key": AUTH_STORAGE_KEY,
        "protected_surfaces": [
            "/api/recipes/manage*",
            "/mcp*",
        ],
    }
