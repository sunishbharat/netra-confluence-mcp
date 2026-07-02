from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from confluence.client import ConfluenceClient
from confluence.export.resolver import resolve_page_id
from exceptions import InvalidUrlError, PageNotFoundError, WrongSiteError
from models.config import NetraSettings

BASE = "https://test.atlassian.net"


async def test_bare_numeric_id(client: ConfluenceClient, settings: NetraSettings) -> None:
    assert await resolve_page_id("4587521", client, settings) == "4587521"


async def test_bare_numeric_id_strips_whitespace(
    client: ConfluenceClient, settings: NetraSettings
) -> None:
    assert await resolve_page_id("  4587521  ", client, settings) == "4587521"


async def test_modern_page_url_with_title(
    client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/spaces/ENG/pages/4587521/Release+Report"
    assert await resolve_page_id(url, client, settings) == "4587521"


async def test_modern_page_url_without_title(
    client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/spaces/ENG/pages/4587521"
    assert await resolve_page_id(url, client, settings) == "4587521"


async def test_modern_page_url_strips_fragment_and_query(
    client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/spaces/ENG/pages/4587521/Release+Report?foo=bar#heading"
    assert await resolve_page_id(url, client, settings) == "4587521"


async def test_legacy_viewpage_action(client: ConfluenceClient, settings: NetraSettings) -> None:
    url = f"{BASE}/pages/viewpage.action?pageId=4587521"
    assert await resolve_page_id(url, client, settings) == "4587521"


async def test_legacy_viewpage_action_missing_page_id(
    client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/pages/viewpage.action?spaceKey=ENG"
    with pytest.raises(InvalidUrlError):
        await resolve_page_id(url, client, settings)


async def test_tiny_link_follows_redirect(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/x/AbCdE"
    httpx_mock.add_response(
        url=url,
        status_code=302,
        headers={"location": "/wiki/spaces/ENG/pages/4587521/Release+Report"},
    )
    assert await resolve_page_id(url, client, settings) == "4587521"


async def test_tiny_link_no_location_header_is_invalid(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/x/AbCdE"
    httpx_mock.add_response(url=url, status_code=200)
    with pytest.raises(InvalidUrlError):
        await resolve_page_id(url, client, settings)


async def test_tiny_link_too_many_redirects_is_invalid(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/x/AbCdE"
    # Every redirect points to another tiny link, never resolving.
    httpx_mock.add_response(
        url=url, status_code=302, headers={"location": "/wiki/x/AbCdE"}, is_reusable=True
    )
    with pytest.raises(InvalidUrlError):
        await resolve_page_id(url, client, settings)


async def test_display_url_follows_redirect(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/display/ENG/Release+Report"
    httpx_mock.add_response(
        url=url,
        status_code=302,
        headers={"location": "/wiki/spaces/ENG/pages/4587521/Release+Report"},
    )
    assert await resolve_page_id(url, client, settings) == "4587521"


async def test_display_url_falls_back_to_cql(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/display/ENG/Release+Report"
    httpx_mock.add_response(url=url, status_code=200)  # no redirect
    httpx_mock.add_response(
        url=f"{BASE}/wiki/rest/api/content/search",
        match_params={"cql": 'space="ENG" and title="Release Report"'},
        json={"results": [{"id": "4587521"}]},
    )
    assert await resolve_page_id(url, client, settings) == "4587521"


async def test_display_url_cql_fallback_not_found(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    url = f"{BASE}/wiki/display/ENG/Release+Report"
    httpx_mock.add_response(url=url, status_code=200)
    httpx_mock.add_response(
        url=f"{BASE}/wiki/rest/api/content/search",
        match_params={"cql": 'space="ENG" and title="Release Report"'},
        json={"results": []},
    )
    with pytest.raises(PageNotFoundError):
        await resolve_page_id(url, client, settings)


# --- Hostile inputs: SSRF / wrong-tenant guard, zero network calls -----------


async def test_evil_host_is_rejected(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    with pytest.raises(WrongSiteError):
        await resolve_page_id("https://evil.example/pages/1", client, settings)
    assert len(httpx_mock.get_requests()) == 0


async def test_http_scheme_is_rejected(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    with pytest.raises(WrongSiteError):
        await resolve_page_id(f"http://{BASE.removeprefix('https://')}/pages/1", client, settings)
    assert len(httpx_mock.get_requests()) == 0


async def test_userinfo_in_url_is_rejected(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    with pytest.raises(WrongSiteError):
        await resolve_page_id("https://attacker@evil.example/pages/1", client, settings)
    assert len(httpx_mock.get_requests()) == 0


async def test_userinfo_disguised_as_real_host_is_rejected(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    """'https://test.atlassian.net@evil.example/...' - userinfo looks like the real host."""
    with pytest.raises(WrongSiteError):
        await resolve_page_id("https://test.atlassian.net@evil.example/pages/1", client, settings)
    assert len(httpx_mock.get_requests()) == 0


async def test_ip_host_is_rejected(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    with pytest.raises(WrongSiteError):
        await resolve_page_id("https://169.254.169.254/pages/1", client, settings)
    assert len(httpx_mock.get_requests()) == 0


async def test_unrecognized_shape_on_correct_host_is_invalid_url(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    with pytest.raises(InvalidUrlError):
        await resolve_page_id(f"{BASE}/wiki/some/unknown/path", client, settings)
    assert len(httpx_mock.get_requests()) == 0


async def test_empty_url_is_invalid(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    with pytest.raises(InvalidUrlError):
        await resolve_page_id("   ", client, settings)
    assert len(httpx_mock.get_requests()) == 0
