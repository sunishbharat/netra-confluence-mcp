from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import confluence.tools.export_pdf as module
from confluence.export.fetcher import ExportPageData
from exceptions import (
    ConfluencePermissionError,
    InvalidUrlError,
    MissingCredentialsError,
    PageNotFoundError,
    PageTooLargeError,
    RateLimitedError,
    StorageFailedError,
    WrongSiteError,
)
from models.export import AssetReport

_FAKE_PDF = b"%PDF-1.4 fake pdf bytes for testing"


def _page_data(*, title: str = "R1.0 Release Report", version: int = 5) -> ExportPageData:
    return ExportPageData(
        page_id="4587521",
        title=title,
        version=version,
        space_key="ENG",
        html="<html><body><h1>hi</h1></body></html>",
    )


def _empty_report() -> AssetReport:
    return AssetReport(fetched=[], skipped_external=[], failed=[], downscaled=[])


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_SITE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "test-token")
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "test@example.com")
    monkeypatch.setenv("SERVER_TRANSPORT", "stdio")


@pytest.fixture
def fake_client() -> MagicMock:
    fake = MagicMock()
    fake.site_url = "https://test.atlassian.net"
    fake.put = AsyncMock()
    fake.post = AsyncMock()
    fake.get = AsyncMock()
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    return fake


@pytest.fixture(autouse=True)
def patch_client(monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock) -> MagicMock:
    monkeypatch.setattr(module, "get_client", lambda: fake_client)
    return fake_client


@pytest.fixture(autouse=True)
def patch_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    resolve_mock = AsyncMock(return_value="4587521")
    fetch_mock = AsyncMock(return_value=_page_data())
    assets_mock = AsyncMock(return_value=("<html><body><h1>hi</h1></body></html>", _empty_report()))
    render_mock = MagicMock(return_value=_FAKE_PDF)

    monkeypatch.setattr(module, "resolve_page_id", resolve_mock)
    monkeypatch.setattr(module, "fetch_export_view", fetch_mock)
    monkeypatch.setattr(module, "localize_assets", assets_mock)
    monkeypatch.setattr(module, "render_pdf", render_mock)

    fake_store = MagicMock()
    fake_store.put = AsyncMock(return_value="/exports/tok123")
    monkeypatch.setattr(module, "get_default_store", lambda max_bytes: fake_store)  # noqa: ARG005

    return {
        "resolve": resolve_mock,
        "fetch": fetch_mock,
        "assets": assets_mock,
        "render": render_mock,
        "store": fake_store,
    }


# --- Happy paths --------------------------------------------------------------


async def test_link_delivery_returns_ok_with_download_url(fake_client: MagicMock) -> None:
    result = await module.export_page_pdf("4587521")
    assert result["status"] == "OK"
    assert result["delivery"] == "link"
    assert result["download_url"] is not None
    assert "/exports/tok123" in result["download_url"]
    assert result["expires_at"] is not None
    assert result["page_id"] == "4587521"
    assert result["page_title"] == "R1.0 Release Report"
    assert result["page_version"] == 5
    assert result["pdf_bytes"] == len(_FAKE_PDF)
    import hashlib

    assert result["pdf_sha256"] == hashlib.sha256(_FAKE_PDF).hexdigest()


async def test_link_delivery_message_repeats_url_and_expiry() -> None:
    result = await module.export_page_pdf("4587521")
    assert result["download_url"] in result["message"]
    assert "expires" in result["message"].lower()


async def test_inline_delivery_returns_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPORT_INLINE_MAX_BYTES", "1000000")
    result = await module.export_page_pdf("4587521", delivery="inline")
    assert result["status"] == "OK"
    assert result["delivery"] == "inline"
    assert result["pdf_base64"] is not None
    import base64

    assert base64.b64decode(result["pdf_base64"]) == _FAKE_PDF


async def test_inline_delivery_too_large_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPORT_INLINE_MAX_BYTES", "1")
    result = await module.export_page_pdf("4587521", delivery="inline")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "TOO_LARGE_FOR_INLINE"


async def test_page_size_is_passed_through(
    patch_pipeline: dict[str, AsyncMock],
) -> None:
    await module.export_page_pdf("4587521", page_size="LETTER")
    _args, kwargs = patch_pipeline["render"].call_args
    assert kwargs["page_size"] == "LETTER"


async def test_custom_filename_gets_pdf_extension(
    patch_pipeline: dict[str, AsyncMock],
) -> None:
    await module.export_page_pdf("4587521", filename="my-report")
    _args, kwargs = patch_pipeline["store"].put.call_args
    stored_filename = patch_pipeline["store"].put.call_args.args[2]
    assert stored_filename == "my-report.pdf"


async def test_default_filename_is_slugified_title(
    patch_pipeline: dict[str, AsyncMock],
) -> None:
    await module.export_page_pdf("4587521")
    stored_filename = patch_pipeline["store"].put.call_args.args[2]
    assert stored_filename.startswith("r1-0-release-report-")
    assert stored_filename.endswith(".pdf")


# --- Error code coverage (addendum section 5 failure-mode matrix) ------------


async def test_invalid_url_error_code(patch_pipeline: dict[str, AsyncMock]) -> None:
    patch_pipeline["resolve"].side_effect = InvalidUrlError("bad shape")
    result = await module.export_page_pdf("not-a-valid-url")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "INVALID_URL"


async def test_wrong_site_error_code(patch_pipeline: dict[str, AsyncMock]) -> None:
    patch_pipeline["resolve"].side_effect = WrongSiteError("wrong host")
    result = await module.export_page_pdf("https://evil.example/pages/1")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "WRONG_SITE"


async def test_page_not_found_error_code(patch_pipeline: dict[str, AsyncMock]) -> None:
    patch_pipeline["fetch"].side_effect = PageNotFoundError("no such page")
    result = await module.export_page_pdf("4587521")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "PAGE_NOT_FOUND"


async def test_permission_denied_error_code(patch_pipeline: dict[str, AsyncMock]) -> None:
    patch_pipeline["fetch"].side_effect = ConfluencePermissionError("no access")
    result = await module.export_page_pdf("4587521")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "PERMISSION_DENIED"


async def test_page_too_large_error_code(patch_pipeline: dict[str, AsyncMock]) -> None:
    patch_pipeline["fetch"].side_effect = PageTooLargeError(measured=6_000_000, cap=5_000_000)
    result = await module.export_page_pdf("4587521")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "PAGE_TOO_LARGE"


async def test_rate_limited_error_code(patch_pipeline: dict[str, AsyncMock]) -> None:
    patch_pipeline["fetch"].side_effect = RateLimitedError("429", retry_after=5.0)
    result = await module.export_page_pdf("4587521")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "RATE_LIMITED"


async def test_storage_failed_error_code(patch_pipeline: dict[str, AsyncMock]) -> None:
    patch_pipeline["store"].put.side_effect = StorageFailedError("cache full")
    result = await module.export_page_pdf("4587521")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "STORAGE_FAILED"
    assert "inline" in result["message"]


async def test_api_error_code_is_the_generic_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> None:
        raise MissingCredentialsError("missing per-user Confluence credentials")

    monkeypatch.setattr(module, "get_client", _raise)
    result = await module.export_page_pdf("4587521")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "API_ERROR"


async def test_export_timeout_error_code(
    monkeypatch: pytest.MonkeyPatch, patch_pipeline: dict[str, AsyncMock]
) -> None:
    import asyncio

    monkeypatch.setenv("EXPORT_TIMEOUT_SECONDS", "0.01")

    async def _slow_resolve(*_args: object, **_kwargs: object) -> str:
        await asyncio.sleep(0.2)
        return "4587521"

    patch_pipeline["resolve"].side_effect = _slow_resolve
    result = await module.export_page_pdf("4587521")
    assert result["status"] == "ERROR"
    assert result["error_code"] == "EXPORT_TIMEOUT"


# --- Read-only invariant -------------------------------------------------------


async def test_no_confluence_write_ever_occurs_on_success(fake_client: MagicMock) -> None:
    await module.export_page_pdf("4587521")
    fake_client.put.assert_not_called()
    fake_client.post.assert_not_called()


async def test_no_confluence_write_ever_occurs_on_error(
    fake_client: MagicMock, patch_pipeline: dict[str, AsyncMock]
) -> None:
    patch_pipeline["fetch"].side_effect = PageNotFoundError("no such page")
    await module.export_page_pdf("4587521")
    fake_client.put.assert_not_called()
    fake_client.post.assert_not_called()


async def test_no_confluence_write_ever_occurs_inline_delivery(fake_client: MagicMock) -> None:
    await module.export_page_pdf("4587521", delivery="inline")
    fake_client.put.assert_not_called()
    fake_client.post.assert_not_called()


# --- Download URL composition --------------------------------------------------


async def test_download_url_uses_live_request_base_url_on_http_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVER_TRANSPORT", "http")
    fake_request = MagicMock()
    fake_request.base_url = "https://netra-confluence.example.com/"
    monkeypatch.setattr("fastmcp.server.dependencies.get_http_request", lambda: fake_request)

    result = await module.export_page_pdf("4587521")

    assert result["download_url"] == "https://netra-confluence.example.com/exports/tok123"


async def test_download_url_falls_back_when_no_http_request_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVER_TRANSPORT", "http")

    def _raise() -> None:
        raise RuntimeError("No active HTTP request found.")

    monkeypatch.setattr("fastmcp.server.dependencies.get_http_request", _raise)

    result = await module.export_page_pdf("4587521")

    assert result["download_url"] == "http://127.0.0.1:8765/exports/tok123"
