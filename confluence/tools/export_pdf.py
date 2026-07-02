from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog

from confluence.export.assets import localize_assets
from confluence.export.fetcher import fetch_export_view
from confluence.export.pdf import render_pdf
from confluence.export.resolver import resolve_page_id
from confluence.export.store import get_default_store
from confluence.tools.shared import get_client
from exceptions import (
    ConfluencePermissionError,
    InvalidUrlError,
    NetraConfluenceError,
    PageNotFoundError,
    PageTooLargeError,
    RateLimitedError,
    StorageFailedError,
    WrongSiteError,
)
from models.config import NetraSettings
from models.export import ExportErrorCode, ExportPdfRequest, ExportPdfResult

log = structlog.get_logger()

# Process-wide: WeasyPrint layout of a large page spikes well beyond the
# httpx-only baseline, so concurrent exports queue instead of stacking RSS
# (addendum section 5, risk register: "WeasyPrint memory spike on
# pathological pages").
_render_semaphore = asyncio.Semaphore(1)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "export"


def _default_filename(title: str) -> str:
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{_slugify(title)}-{date_str}.pdf"


def _error_result(code: ExportErrorCode, message: str) -> dict[str, Any]:
    return ExportPdfResult(status="ERROR", error_code=code, message=message).model_dump(mode="json")


def _build_download_url(path: str, settings: NetraSettings) -> str:
    """Compose an absolute download URL for a store-relative path.

    Only the live HTTP request knows the externally-visible host (CF's
    router hostname, not the container's bind host/port) - see the
    deviation note on ExportStore.put in confluence/export/store.py.
    """
    if settings.server_transport == "http":
        try:
            from fastmcp.server.dependencies import get_http_request

            request = get_http_request()
            return str(request.base_url).rstrip("/") + path
        except RuntimeError:
            pass  # no active HTTP request context - fall back below

    scheme = "http" if settings.server_host in ("127.0.0.1", "localhost", "0.0.0.0") else "https"
    return f"{scheme}://{settings.server_host}:{settings.server_port}{path}"


async def export_page_pdf(
    page_url: str,
    delivery: Literal["link", "inline"] = "link",
    page_size: Literal["A4", "LETTER"] = "A4",
    filename: str | None = None,
) -> dict[str, Any]:
    """
    Export any Confluence page to a PDF, rendered server-side from
    Confluence's own export_view HTML (Jira macros render to static tables -
    no headless browser). Fully read-only against Confluence: no page,
    attachment, or metadata write ever occurs.

    page_url accepts a bare page id or any Confluence URL shape (modern page
    URL, legacy viewpage.action, tiny link, display URL) for the configured
    site only - links to any other host are rejected before any network call.

    delivery="link" (default) uploads the PDF to a time-limited download URL
    and returns it with its expiry. delivery="inline" returns the PDF as
    base64, but only when it fits under the inline size cap - otherwise use
    "link".
    """
    request = ExportPdfRequest(
        page_url=page_url, delivery=delivery, page_size=page_size, filename=filename
    )
    settings = NetraSettings()  # type: ignore[call-arg]  # fields read from env

    try:
        async with get_client() as client:
            async with asyncio.timeout(settings.export_timeout_seconds):
                page_id = await resolve_page_id(request.page_url, client, settings)
                page = await fetch_export_view(client, page_id, settings)
                localized_html, asset_report = await localize_assets(
                    page.html, client=client, settings=settings
                )

                async with _render_semaphore:
                    pdf_bytes = await asyncio.to_thread(
                        render_pdf,
                        localized_html,
                        title=page.title,
                        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
                        page_size=request.page_size,
                    )
    except TimeoutError:
        log.error("export_page_pdf timed out", page_url=page_url)
        return _error_result(
            "EXPORT_TIMEOUT",
            f"Export exceeded the {settings.export_timeout_seconds}s time budget.",
        )
    except InvalidUrlError as e:
        return _error_result("INVALID_URL", str(e))
    except WrongSiteError as e:
        return _error_result("WRONG_SITE", str(e))
    except PageNotFoundError as e:
        return _error_result("PAGE_NOT_FOUND", str(e))
    except ConfluencePermissionError as e:
        return _error_result("PERMISSION_DENIED", str(e))
    except PageTooLargeError as e:
        return _error_result("PAGE_TOO_LARGE", str(e))
    except RateLimitedError as e:
        return _error_result("RATE_LIMITED", str(e))
    except NetraConfluenceError as e:
        log.error("export_page_pdf failed", page_url=page_url, error=str(e))
        return _error_result("API_ERROR", str(e))

    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    pdf_size = len(pdf_bytes)
    resolved_filename = request.filename or _default_filename(page.title)
    if not resolved_filename.lower().endswith(".pdf"):
        resolved_filename += ".pdf"

    if request.delivery == "inline":
        if pdf_size > settings.export_inline_max_bytes:
            return _error_result(
                "TOO_LARGE_FOR_INLINE",
                f"PDF is {pdf_size} bytes, exceeds the inline delivery cap of "
                f"{settings.export_inline_max_bytes} bytes. Use delivery='link' instead.",
            )
        result = ExportPdfResult(
            status="OK",
            page_id=page.page_id,
            page_title=page.title,
            page_version=page.version,
            pdf_sha256=pdf_sha256,
            pdf_bytes=pdf_size,
            delivery="inline",
            pdf_base64=base64.b64encode(pdf_bytes).decode("ascii"),
            asset_report=asset_report,
            message=(
                f"Rendered {resolved_filename} ({pdf_size} bytes) and returned it inline as base64."
            ),
        )
        return result.model_dump(mode="json")

    try:
        store = get_default_store(max_bytes=settings.export_store_max_bytes)
        token = secrets.token_urlsafe(32)
        path = await store.put(
            token, pdf_bytes, resolved_filename, settings.export_link_ttl_seconds
        )
    except StorageFailedError as e:
        return _error_result("STORAGE_FAILED", f"{e} Try delivery='inline' for small PDFs.")
    except NetraConfluenceError as e:
        log.error("export_page_pdf store.put failed", page_url=page_url, error=str(e))
        return _error_result("API_ERROR", str(e))

    expires_at = datetime.now(UTC) + timedelta(seconds=settings.export_link_ttl_seconds)
    download_url = _build_download_url(path, settings)

    result = ExportPdfResult(
        status="OK",
        page_id=page.page_id,
        page_title=page.title,
        page_version=page.version,
        pdf_sha256=pdf_sha256,
        pdf_bytes=pdf_size,
        delivery="link",
        download_url=download_url,
        expires_at=expires_at,
        asset_report=asset_report,
        message=(
            f"PDF ready: {download_url} (expires {expires_at.isoformat()}). "
            "Open the link in a browser to download it."
        ),
    )
    return result.model_dump(mode="json")
