"""Unit tests for access-token authentication helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.datastructures import Headers

from web2api.auth import AuthConfig, load_auth_config, request_is_authorized


def test_auth_config_loads_token_from_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_file = tmp_path / "token.txt"
    secret_file.write_text("secret-token\n", encoding="utf-8")
    monkeypatch.delenv("WEB2API_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("WEB2API_ACCESS_TOKEN_FILE", str(secret_file))

    config = load_auth_config()

    assert config.enabled is True
    assert config.access_token == "secret-token"


def test_request_is_authorized_supports_bearer_and_alt_header() -> None:
    config = AuthConfig(access_token="secret-token")

    assert request_is_authorized(
        Headers({"authorization": "Bearer secret-token"}),
        config,
    ) is True
    assert request_is_authorized(
        Headers({"x-web2api-key": "secret-token"}),
        config,
    ) is True
    assert request_is_authorized(
        Headers({"authorization": "Bearer wrong"}),
        config,
    ) is False
