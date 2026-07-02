from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import confluence.tools.create_page as module
from exceptions import ConfluenceAPIError
from models.confluence import PageMetadata

_VALID_ADF: dict[str, Any] = {
    "type": "doc",
    "version": 1,
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "hello"}]},
    ],
}

_INVALID_ADF: dict[str, Any] = {"type": "paragraph", "content": []}


def _make_meta(page_id: str = "999", version: int = 1) -> PageMetadata:
    return PageMetadata(
        id=page_id, title="New Page", version=version, space_id="SPACE001", status="current"
    )


def _space_lookup_response(space_id: str = "SPACE001") -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"results": [{"id": space_id}]}
    return response


@pytest.fixture(autouse=True)
def patch_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    fake.site_url = "https://test.atlassian.net"
    fake.get = AsyncMock(return_value=_space_lookup_response())
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(module, "get_client", lambda: fake)
    return fake


async def test_dry_run_returns_dry_run_status() -> None:
    result = await module.create_page_from_adf("SPACE001", "New Page", _VALID_ADF, dry_run=True)
    assert result["status"] == "DRY_RUN"


async def test_dry_run_does_not_touch_client(patch_client: MagicMock) -> None:
    await module.create_page_from_adf("SPACE001", "New Page", _VALID_ADF, dry_run=True)
    patch_client.get.assert_not_called()


async def test_validation_error_returns_validation_failed() -> None:
    result = await module.create_page_from_adf("SPACE001", "New Page", _INVALID_ADF, dry_run=True)
    assert result["status"] == "VALIDATION_FAILED"
    assert isinstance(result["errors"], list)


async def test_validation_runs_before_write(patch_client: MagicMock) -> None:
    """Invalid ADF must short-circuit even when dry_run=False - never reach get_client."""
    result = await module.create_page_from_adf(
        "SPACE001", "New Page", _INVALID_ADF, dry_run=False
    )
    assert result["status"] == "VALIDATION_FAILED"
    patch_client.get.assert_not_called()


async def test_apply_resolves_space_and_creates_page(
    monkeypatch: pytest.MonkeyPatch, patch_client: MagicMock
) -> None:
    mock_create_page = AsyncMock(return_value=_make_meta())
    monkeypatch.setattr(module, "create_page", mock_create_page)
    result = await module.create_page_from_adf("SPACE001", "New Page", _VALID_ADF, dry_run=False)
    assert result["status"] == "CREATED"
    assert result["page_id"] == "999"
    mock_create_page.assert_called_once()
    request = mock_create_page.call_args.args[1]
    assert request.space_id == "SPACE001"
    assert request.title == "New Page"


async def test_apply_returns_url(monkeypatch: pytest.MonkeyPatch, patch_client: MagicMock) -> None:
    monkeypatch.setattr(module, "create_page", AsyncMock(return_value=_make_meta()))
    result = await module.create_page_from_adf("SPACE001", "New Page", _VALID_ADF, dry_run=False)
    assert "url" in result
    assert "999" in str(result["url"])


async def test_apply_passes_parent_page_id(
    monkeypatch: pytest.MonkeyPatch, patch_client: MagicMock
) -> None:
    mock_create_page = AsyncMock(return_value=_make_meta())
    monkeypatch.setattr(module, "create_page", mock_create_page)
    await module.create_page_from_adf(
        "SPACE001", "New Page", _VALID_ADF, parent_page_id="parent-1", dry_run=False
    )
    request = mock_create_page.call_args.args[1]
    assert request.parent_id == "parent-1"


async def test_space_not_found_returns_error_status(patch_client: MagicMock) -> None:
    response = MagicMock()
    response.json.return_value = {"results": []}
    patch_client.get = AsyncMock(return_value=response)
    result = await module.create_page_from_adf("MISSING", "New Page", _VALID_ADF, dry_run=False)
    assert result["status"] == "ERROR"
    assert "MISSING" in str(result["error"])


async def test_create_page_error_returns_error_status(
    monkeypatch: pytest.MonkeyPatch, patch_client: MagicMock
) -> None:
    monkeypatch.setattr(module, "create_page", AsyncMock(side_effect=ConfluenceAPIError("boom")))
    result = await module.create_page_from_adf("SPACE001", "New Page", _VALID_ADF, dry_run=False)
    assert result["status"] == "ERROR"
    assert "boom" in str(result["error"])
