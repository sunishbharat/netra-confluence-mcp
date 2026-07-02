from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from confluence.client import ConfluenceClient
from confluence.export.fetcher import fetch_export_view
from exceptions import ConfluencePermissionError, PageNotFoundError, PageTooLargeError
from models.config import NetraSettings

BASE = "https://test.atlassian.net"
PAGE_ID = "4587521"


def _response_body(html: str, *, version: int = 5, space_key: str = "ENG") -> dict[str, object]:
    return {
        "id": PAGE_ID,
        "title": "Release Report",
        "version": {"number": version},
        "space": {"key": space_key},
        "body": {"export_view": {"value": html}},
        "history": {"lastUpdated": {"when": "2026-07-01T00:00:00.000Z"}},
    }


async def test_fetch_export_view_returns_page_data(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/rest/api/content/{PAGE_ID}",
        match_params={"expand": "body.export_view,version,space,history.lastUpdated"},
        json=_response_body("<html><body><h1>Hi</h1></body></html>"),
    )
    result = await fetch_export_view(client, PAGE_ID, settings)
    assert result.page_id == PAGE_ID
    assert result.title == "Release Report"
    assert result.version == 5
    assert result.space_key == "ENG"
    assert "<h1>Hi</h1>" in result.html


async def test_fetch_export_view_over_cap_raises_page_too_large(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    settings.export_max_html_bytes = 10
    httpx_mock.add_response(
        url=f"{BASE}/wiki/rest/api/content/{PAGE_ID}",
        match_params={"expand": "body.export_view,version,space,history.lastUpdated"},
        json=_response_body("<html>" + "x" * 100 + "</html>"),
    )
    with pytest.raises(PageTooLargeError) as exc_info:
        await fetch_export_view(client, PAGE_ID, settings)
    assert exc_info.value.cap == 10
    assert exc_info.value.measured > 10


async def test_fetch_export_view_403_propagates_permission_error(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/rest/api/content/{PAGE_ID}",
        match_params={"expand": "body.export_view,version,space,history.lastUpdated"},
        status_code=403,
    )
    with pytest.raises(ConfluencePermissionError):
        await fetch_export_view(client, PAGE_ID, settings)


async def test_fetch_export_view_404_propagates_page_not_found(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/rest/api/content/{PAGE_ID}",
        match_params={"expand": "body.export_view,version,space,history.lastUpdated"},
        status_code=404,
    )
    with pytest.raises(PageNotFoundError):
        await fetch_export_view(client, PAGE_ID, settings)
