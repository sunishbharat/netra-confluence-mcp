from __future__ import annotations

import html as html_lib
import importlib.resources
from typing import Any, Literal

_PAGE_SIZE_CSS = {"A4": "A4", "LETTER": "letter"}


def no_network_url_fetcher(
    url: str,
    timeout: float | None = None,
    ssl_context: Any = None,  # noqa: ANN401, ARG001
) -> Any:  # noqa: ANN401
    """WeasyPrint url_fetcher that raises on any URL.

    After asset localization there is nothing legitimate left to fetch - any
    attempt here means an un-inlined URL leaked into the HTML, which is a bug
    or an injection. Fail loudly rather than let the render reach the network.
    """
    raise ValueError(f"network fetch blocked during PDF render: {url}")


def _load_base_css() -> str:
    return (
        importlib.resources.files("confluence.export")
        .joinpath("print.css")
        .read_text(encoding="utf-8")
    )


def _wrap_html(html_body: str, *, title: str, timestamp: str) -> str:
    """Inject the invisible title/timestamp marker paragraphs print.css reads via
    string-set, as the first children of <body> so they fire before page content.
    """
    marker = (
        f'<p class="netra-export-marker">{html_lib.escape(title)}</p>'
        f'<p class="netra-export-marker netra-export-timestamp">{html_lib.escape(timestamp)}</p>'
    )
    lowered = html_body.lower()
    body_idx = lowered.find("<body")
    if body_idx == -1:
        return marker + html_body
    close_idx = html_body.find(">", body_idx)
    if close_idx == -1:
        return marker + html_body
    insert_at = close_idx + 1
    return html_body[:insert_at] + marker + html_body[insert_at:]


def render_pdf(
    html_body: str,
    *,
    title: str,
    timestamp: str,
    page_size: Literal["A4", "LETTER"],
) -> bytes:
    """Render already-localized, offline-safe HTML to PDF bytes.

    Synchronous and CPU-bound - callers must run this via asyncio.to_thread,
    never directly on the event loop of a stateless multi-request server.
    """
    # Lazy import: importing weasyprint requires system libs (Pango, Cairo,
    # GDK-Pixbuf, HarfBuzz) that are not present on every machine that imports
    # this module's package (e.g. local dev without the Docker image's apt
    # packages). Deferring the import keeps the rest of the export pipeline -
    # and every test that doesn't actually render - importable everywhere.
    from weasyprint import CSS, HTML

    full_html = _wrap_html(html_body, title=title, timestamp=timestamp)
    base_css = _load_base_css()
    size_css = f"@page {{ size: {_PAGE_SIZE_CSS[page_size]}; }}"

    document = HTML(string=full_html, url_fetcher=no_network_url_fetcher)
    pdf_bytes: bytes = document.write_pdf(stylesheets=[CSS(string=base_css), CSS(string=size_css)])
    return pdf_bytes
