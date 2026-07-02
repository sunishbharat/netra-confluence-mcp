from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.config import NetraSettings

# _env_file=None disables the repo's own .env (which has real-looking placeholder
# values) so these tests only see what they explicitly pass in - not local dev config.


def test_stdio_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    monkeypatch.delenv("CONFLUENCE_USER_EMAIL", raising=False)
    with pytest.raises(ValidationError):
        NetraSettings(
            _env_file=None,  # type: ignore[call-arg]
            confluence_base_url="https://test.atlassian.net",
            confluence_site_url="https://test.atlassian.net",
            server_transport="stdio",
        )


def test_stdio_accepts_explicit_credentials() -> None:
    settings = NetraSettings(
        _env_file=None,  # type: ignore[call-arg]
        confluence_base_url="https://test.atlassian.net",
        confluence_site_url="https://test.atlassian.net",
        confluence_api_token="test-token",
        confluence_user_email="test@example.com",
        server_transport="stdio",
    )
    assert settings.confluence_api_token == "test-token"


def test_http_does_not_require_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 1: the http transport server owns no Confluence identity at all."""
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    monkeypatch.delenv("CONFLUENCE_USER_EMAIL", raising=False)
    settings = NetraSettings(
        _env_file=None,  # type: ignore[call-arg]
        confluence_base_url="https://test.atlassian.net",
        confluence_site_url="https://test.atlassian.net",
        server_transport="http",
    )
    assert settings.confluence_api_token is None
    assert settings.confluence_user_email is None
