from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from confluence.client import ConfluenceClient
from exceptions import (
    ConfluenceAPIError,
    ConfluencePermissionError,
    PageNotFoundError,
    VersionConflictError,
)
from models.config import NetraSettings

BASE = "https://test.atlassian.net"


async def test_get_sends_basic_auth(httpx_mock: HTTPXMock, client: ConfluenceClient) -> None:
    httpx_mock.add_response(url=f"{BASE}/wiki/test", json={})
    await client.get("/wiki/test")
    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"].startswith("Basic ")


async def test_get_sets_accept_header(httpx_mock: HTTPXMock, client: ConfluenceClient) -> None:
    httpx_mock.add_response(url=f"{BASE}/wiki/test", json={})
    await client.get("/wiki/test")
    request = httpx_mock.get_requests()[0]
    assert request.headers["accept"] == "application/json"


async def test_403_raises_permission_error(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(url=f"{BASE}/wiki/test", status_code=403)
    with pytest.raises(ConfluencePermissionError):
        await client.get("/wiki/test")


async def test_404_raises_page_not_found(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(url=f"{BASE}/wiki/test", status_code=404)
    with pytest.raises(PageNotFoundError):
        await client.get("/wiki/test")


async def test_409_raises_version_conflict(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(url=f"{BASE}/wiki/test", status_code=409)
    with pytest.raises(VersionConflictError):
        await client.get("/wiki/test")


async def test_500_raises_confluence_api_error(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(url=f"{BASE}/wiki/test", status_code=500, text="Internal Server Error")
    with pytest.raises(ConfluenceAPIError):
        await client.get("/wiki/test")


async def test_put_maps_errors(httpx_mock: HTTPXMock, client: ConfluenceClient) -> None:
    httpx_mock.add_response(url=f"{BASE}/wiki/test", method="PUT", status_code=409)
    with pytest.raises(VersionConflictError):
        await client.put("/wiki/test", json={})


async def test_post_maps_errors(httpx_mock: HTTPXMock, client: ConfluenceClient) -> None:
    httpx_mock.add_response(url=f"{BASE}/wiki/test", method="POST", status_code=403)
    with pytest.raises(ConfluencePermissionError):
        await client.post("/wiki/test", json={})


async def test_client_is_async_context_manager(settings: NetraSettings) -> None:
    async with ConfluenceClient(settings) as c:
        assert isinstance(c, ConfluenceClient)


async def test_site_url_property(client: ConfluenceClient) -> None:
    assert client.site_url == "https://test.atlassian.net"
