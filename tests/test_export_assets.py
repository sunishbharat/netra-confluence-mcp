from __future__ import annotations

from pytest_httpx import HTTPXMock

from confluence.client import ConfluenceClient
from confluence.export.assets import localize_assets
from models.config import NetraSettings

BASE = "https://test.atlassian.net"

_TINY_PNG = b"\x89PNG\r\n\x1a\nnot-a-real-png-but-bytes"


async def test_script_tags_are_stripped(client: ConfluenceClient, settings: NetraSettings) -> None:
    html = "<html><body><script>alert(1)</script><p>hi</p></body></html>"
    out, _report = await localize_assets(html, client=client, settings=settings)
    assert "<script>" not in out
    assert "<p>hi</p>" in out


async def test_same_site_image_is_fetched_and_inlined(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/download/attachments/1/pic.png",
        content=_TINY_PNG,
        headers={"content-type": "image/png"},
    )
    html = '<html><body><img src="/download/attachments/1/pic.png"></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert "data:image/png;base64," in out
    assert len(report.fetched) == 1
    assert report.failed == []
    assert report.skipped_external == []


async def test_same_site_fetch_uses_confluence_auth(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(url=f"{BASE}/download/attachments/1/pic.png", content=_TINY_PNG)
    html = '<html><body><img src="/download/attachments/1/pic.png"></body></html>'
    await localize_assets(html, client=client, settings=settings)
    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"].startswith("Basic ")


async def test_media_cdn_image_is_fetched_anonymously(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    media_url = "https://abc123.media.atlassian.com/file/xyz"
    httpx_mock.add_response(url=media_url, content=_TINY_PNG, headers={"content-type": "image/png"})
    html = f'<html><body><img src="{media_url}"></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert "data:image/png;base64," in out
    assert len(report.fetched) == 1
    request = httpx_mock.get_requests()[0]
    assert "authorization" not in request.headers, "media CDN fetch must never carry credentials"


async def test_external_image_becomes_placeholder(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    html = '<html><body><img src="https://cdn.example.com/logo.png"></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert "<img" not in out
    assert "external image omitted: cdn.example.com" in out
    assert report.skipped_external == ["https://cdn.example.com/logo.png"]
    assert report.fetched == []
    assert report.failed == []
    assert len(httpx_mock.get_requests()) == 0, "external hosts must never be fetched"


async def test_oversized_asset_degrades_to_placeholder(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    settings.export_max_asset_bytes = 4
    httpx_mock.add_response(url=f"{BASE}/download/attachments/1/pic.png", content=_TINY_PNG)
    html = '<html><body><img src="/download/attachments/1/pic.png"></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert "<img" not in out
    assert "asset too large" in out
    assert report.failed == [f"asset too large: {BASE}/download/attachments/1/pic.png"]
    assert report.fetched == []


async def test_asset_count_cap_placeholders_extra_assets(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    settings.export_max_assets = 1
    httpx_mock.add_response(url=f"{BASE}/a.png", content=_TINY_PNG)
    html = '<html><body><img src="/a.png"><img src="/b.png"><img src="/c.png"></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert len(report.fetched) == 1
    assert len(report.failed) == 2
    assert all("asset count cap exceeded" in entry for entry in report.failed)
    # Only the first image should have triggered a real fetch.
    assert len(httpx_mock.get_requests()) == 1


async def test_total_byte_budget_caps_later_assets(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    settings.export_max_total_asset_bytes = len(_TINY_PNG)
    httpx_mock.add_response(url=f"{BASE}/a.png", content=_TINY_PNG)
    html = '<html><body><img src="/a.png"><img src="/b.png"></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert len(report.fetched) == 1
    assert len(report.failed) == 1
    assert "total asset budget exceeded" in report.failed[0]
    assert len(httpx_mock.get_requests()) == 1, "the second asset must not even be fetched"


async def test_permission_denied_asset_degrades_to_placeholder(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(url=f"{BASE}/restricted.png", status_code=403)
    html = '<html><body><img src="/restricted.png"></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert "<img" not in out
    assert report.failed == [f"permission denied: {BASE}/restricted.png"]


async def test_not_found_asset_degrades_to_placeholder(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(url=f"{BASE}/missing.png", status_code=404)
    html = '<html><body><img src="/missing.png"></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert "<img" not in out
    assert report.failed == [f"not found: {BASE}/missing.png"]


async def test_style_attribute_url_is_rewritten(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/bg.png", content=_TINY_PNG, headers={"content-type": "image/png"}
    )
    html = "<html><body><div style=\"background: url('/bg.png') no-repeat;\"></div></body></html>"
    out, report = await localize_assets(html, client=client, settings=settings)
    assert "data:image/png;base64," in out
    assert "/bg.png" not in out
    assert len(report.fetched) == 1


async def test_svg_image_href_is_rewritten(
    httpx_mock: HTTPXMock, client: ConfluenceClient, settings: NetraSettings
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/icon.png", content=_TINY_PNG, headers={"content-type": "image/png"}
    )
    html = '<html><body><svg><image href="/icon.png"></image></svg></body></html>'
    out, report = await localize_assets(html, client=client, settings=settings)
    assert "data:image/png;base64," in out
    assert "/icon.png" not in out
    assert len(report.fetched) == 1
