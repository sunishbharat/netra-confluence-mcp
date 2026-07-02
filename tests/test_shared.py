from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import tenacity

import confluence.tools.shared as module
from exceptions import AdfValidationError, MissingCredentialsError, VersionConflictError
from models.adf import ChangeLogEntry
from models.confluence import PageContent, PageMetadata

_PAGE_ID = "123456"
_CHANGE = ChangeLogEntry(location="jql:content/0", detail="'R1.0' -> 'R2.0'")


def _make_page(version: int) -> PageContent:
    return PageContent(
        id=_PAGE_ID,
        title="R1.0 Report",
        version=version,
        space_id="SPACE001",
        status="current",
        adf={"type": "doc", "version": 1, "content": []},
    )


def _make_meta(version: int) -> PageMetadata:
    return PageMetadata(
        id=_PAGE_ID,
        title="R2.0 Report",
        version=version,
        space_id="SPACE001",
        status="current",
    )


@pytest.fixture
def patch_wait_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the exponential backoff with a zero wait so tests run instantly."""
    # tenacity's @retry decorator attaches the retry policy to the wrapped
    # function at runtime; the type stub does not expose `.retry`.
    retry_obj = module.safe_update.retry  # type: ignore[attr-defined]
    monkeypatch.setattr(retry_obj, "wait", tenacity.wait_none())


@pytest.fixture
def fake_client() -> MagicMock:
    return MagicMock(site_url="https://test.atlassian.net")


async def test_safe_update_succeeds_first_try(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """No 409 -> safe_update returns after a single read + write."""
    read_mock = AsyncMock(return_value=_make_page(5))
    write_mock = AsyncMock(return_value=_make_meta(6))
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, [_CHANGE]

    meta, log = await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert meta is not None
    assert meta.version == 6
    assert log == [_CHANGE]
    assert read_mock.call_count == 1
    assert write_mock.call_count == 1


async def test_safe_update_retries_on_409_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """Two 409s then a success -> 3 reads, 3 writes, return final result."""
    read_mock = AsyncMock(side_effect=[_make_page(5), _make_page(6), _make_page(7)])
    write_mock = AsyncMock(
        side_effect=[
            VersionConflictError("concurrent edit 1"),
            VersionConflictError("concurrent edit 2"),
            _make_meta(8),
        ]
    )
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, [_CHANGE]

    meta, log = await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert meta is not None
    assert meta.version == 8
    assert log == [_CHANGE]
    assert read_mock.call_count == 3, "safe_update must re-read on every retry"
    assert write_mock.call_count == 3, "safe_update must retry writes on 409"


async def test_safe_update_raises_after_three_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """Three consecutive 409s -> VersionConflictError propagates after 3 attempts."""
    read_mock = AsyncMock(side_effect=[_make_page(5), _make_page(6), _make_page(7)])
    write_mock = AsyncMock(side_effect=VersionConflictError("always conflicts"))
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, [_CHANGE]

    with pytest.raises(VersionConflictError):
        await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert read_mock.call_count == 3
    assert write_mock.call_count == 3


async def test_safe_update_returns_transform_log(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """The change_log produced by transform_fn is returned alongside the metadata."""
    from models.adf import ChangeLogEntry

    read_mock = AsyncMock(return_value=_make_page(5))
    write_mock = AsyncMock(return_value=_make_meta(6))
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    entry = ChangeLogEntry(location="jql:content/0", detail="'R1.0' -> 'R2.0'")

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, [entry]

    meta, log = await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert meta is not None
    assert meta.version == 6
    assert log == [entry]


async def test_safe_update_skips_write_when_no_changes(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """An empty change_log from transform_fn skips update_page entirely."""
    read_mock = AsyncMock(return_value=_make_page(5))
    write_mock = AsyncMock(return_value=_make_meta(6))
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, []

    meta, log = await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert meta is None
    assert log == []
    assert write_mock.call_count == 0, "no write should happen when nothing changed"


async def test_safe_update_validates_the_adf_it_is_about_to_write(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """The ADF actually produced by transform_fn is validated - not a throwaway preview."""
    read_mock = AsyncMock(return_value=_make_page(5))
    write_mock = AsyncMock(return_value=_make_meta(6))
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        # Invalid: root type is not "doc".
        return {"type": "paragraph", "content": []}, title, [_CHANGE]

    with pytest.raises(AdfValidationError) as exc_info:
        await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert exc_info.value.errors
    assert write_mock.call_count == 0, "an invalid ADF must never reach update_page"


async def test_update_date_nodes_does_not_mutate_input() -> None:
    """update_date_nodes must deep-copy internally so the caller's ADF is safe."""
    adf: dict[str, Any] = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "date", "attrs": {"timestamp": "1778025600000"}},
        ],
    }
    import copy

    original = copy.deepcopy(adf)

    new_adf, log = module.update_date_nodes(adf, "2026-09-15")

    # Input is untouched.
    assert adf == original
    # Output is a different object with the new timestamp.
    assert new_adf is not adf
    assert new_adf["content"][0]["attrs"]["timestamp"] != "1778025600000"
    assert log, "expected a date change log entry"


def _set_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server-owned config that both stdio and http transports need regardless of identity."""
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_SITE_URL", "https://test.atlassian.net")


async def test_get_client_stdio_uses_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """stdio transport (Tier 0): one process per user, so env credentials are the identity."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("SERVER_TRANSPORT", "stdio")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "env-token")
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "env-user@example.com")

    async with module.get_client() as client:
        assert client.site_url == "https://test.atlassian.net"


async def test_get_client_http_without_headers_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """http transport (Tier 1) with no credential headers is a hard error, not a fallback."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("SERVER_TRANSPORT", "http")
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    monkeypatch.delenv("CONFLUENCE_USER_EMAIL", raising=False)
    monkeypatch.setattr(module, "get_http_headers", lambda: {})

    with pytest.raises(MissingCredentialsError):
        module.get_client()


async def test_get_client_http_uses_per_request_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """http transport builds a client from the caller's own X-Confluence-* headers."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("SERVER_TRANSPORT", "http")
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    monkeypatch.delenv("CONFLUENCE_USER_EMAIL", raising=False)
    monkeypatch.setattr(
        module,
        "get_http_headers",
        lambda: {
            "x-confluence-user-email": "alice@example.com",
            "x-confluence-api-token": "alice-token",
        },
    )

    async with module.get_client() as client:
        assert client.site_url == "https://test.atlassian.net"


async def test_get_client_http_never_falls_back_to_env_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if the server process has service-account env vars set, http must ignore them."""
    _set_base_env(monkeypatch)
    monkeypatch.setenv("SERVER_TRANSPORT", "http")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "service-account-token")
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "service-account@example.com")
    monkeypatch.setattr(module, "get_http_headers", lambda: {})

    with pytest.raises(MissingCredentialsError):
        module.get_client()
