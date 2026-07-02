from __future__ import annotations

import logging
import os
import re

import structlog
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from confluence.export.store import get_default_store
from confluence.tools.clone_report import clone_release_report
from confluence.tools.create_page import create_page_from_adf
from confluence.tools.export_pdf import export_page_pdf
from confluence.tools.inspect_jql import inspect_page_jql
from confluence.tools.update_macros import update_page_macros
from confluence.tools.update_release import update_release_version
from models.config import NetraSettings

logger = structlog.get_logger(__name__)

# Any control character (including CR/LF, which would let a crafted filename
# inject additional response headers) or a bare double quote (which would let
# it break out of the quoted filename value) is replaced with "_".
_UNSAFE_HEADER_CHARS_RE = re.compile(r'[\x00-\x1f\x7f"]')


def _safe_content_disposition_filename(filename: str) -> str:
    return _UNSAFE_HEADER_CHARS_RE.sub("_", filename)

server = FastMCP(
    "netra-confluence-writer",
    instructions="Confluence page inspection and write operations via ADF transformation",
)

server.tool()(inspect_page_jql)
server.tool()(update_page_macros)
server.tool()(update_release_version)
server.tool()(clone_release_report)
server.tool()(create_page_from_adf)
server.tool()(export_page_pdf)


@server.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


@server.custom_route("/exports/{token}", methods=["GET"])
async def download_export(request: Request) -> Response:
    """Serve a PDF minted by export_page_pdf(delivery="link"). Only tokens
    put() actually stored are servable - there is no other path this route
    can be coaxed into reading. Expired or unknown tokens get 410 Gone, not a
    stack trace or a 404 that implies the token might exist somewhere else.
    """
    token = request.path_params["token"]
    settings = NetraSettings()  # type: ignore[call-arg]  # fields read from env
    store = get_default_store(max_bytes=settings.export_store_max_bytes)
    result = await store.get(token)
    if result is None:
        return PlainTextResponse("link expired - re-run the export", status_code=410)

    pdf_bytes, filename = result
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_content_disposition_filename(filename)}"'
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


# Phase 4.3: matches event-dict keys carrying credential material, e.g. "token",
# "api_key", "confluence_api_token", "authorization" - not an exhaustive header list,
# a broad key-name match so a future log call naming a secret is redacted by default.
_REDACTED_KEY_PATTERN = re.compile(r"token|password|api_key|authorization", re.IGNORECASE)
_REDACTED_VALUE = "[REDACTED]"


def _redact_sensitive_fields(
    logger: structlog.typing.WrappedLogger,
    method_name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """structlog processor: mask values of keys matching token/password/api_key/authorization."""
    for key in event_dict:
        if _REDACTED_KEY_PATTERN.search(key):
            event_dict[key] = _REDACTED_VALUE
    return event_dict


def _configure_logging(*, json_logs: bool, log_level: str) -> None:
    """structlog: console output for local dev, JSON lines for the CF log drain."""
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)
    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_sensitive_fields,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


def _warn_if_unsafe_memory_export_store(settings: NetraSettings) -> None:
    """EXPORT_STORE=memory holds PDFs only in this one process's RAM - a
    download 404s/410s if it lands on a different instance than the one that
    rendered it (addendum section 4). Only safe at instances: 1, or with
    session affinity, which section 15 deliberately avoids for the rest of
    this server. CF_INSTANCE_INDEX != "0" is proof-positive that more than
    one instance is running; index "0" alone doesn't prove the opposite, but
    it is the only scale-out signal CF gives the app by default.
    """
    if settings.export_store != "memory":
        return
    instance_index = os.environ.get("CF_INSTANCE_INDEX")
    if instance_index is not None and instance_index != "0":
        logger.warning(
            "export_store_memory_scale_out_risk",
            cf_instance_index=instance_index,
            message=(
                "EXPORT_STORE=memory but CF_INSTANCE_INDEX indicates more than one "
                "instance is running - export_page_pdf download links will 404/410 "
                "when they land on a different instance than the one that rendered "
                "the PDF. Keep instances: 1 until EXPORT_STORE=s3 is available."
            ),
        )


if __name__ == "__main__":
    settings = NetraSettings()  # type: ignore[call-arg]  # fields read from env
    _configure_logging(json_logs=settings.json_logs, log_level=settings.log_level)
    _warn_if_unsafe_memory_export_store(settings)
    logger.info(
        "server_starting",
        transport=settings.server_transport,
        host=settings.server_host,
        port=settings.server_port,
    )
    # FastMCP 2.x takes host/port/stateless_http as run() kwargs rather than constructor
    # or post-construction settings; run_stdio_async() doesn't accept them at all, so they
    # are only passed for the http transport.
    if settings.server_transport == "http":
        # Every tool call is an independent read-transform-write against Confluence - there
        # is no per-session state to keep, so http transport always runs stateless (no
        # session affinity required, safe to scale horizontally behind the CF router).
        server.run(
            transport="http",
            host=settings.server_host,
            port=settings.server_port,
            stateless_http=True,
        )
    else:
        server.run(transport="stdio")
