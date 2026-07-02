from __future__ import annotations

import logging
import re

import structlog
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from confluence.tools.clone_report import clone_release_report
from confluence.tools.create_page import create_page_from_adf
from confluence.tools.inspect_jql import inspect_page_jql
from confluence.tools.update_macros import update_page_macros
from confluence.tools.update_release import update_release_version
from models.config import NetraSettings

logger = structlog.get_logger(__name__)

server = FastMCP(
    "netra-confluence-writer",
    instructions="Confluence page inspection and write operations via ADF transformation",
)

server.tool()(inspect_page_jql)
server.tool()(update_page_macros)
server.tool()(update_release_version)
server.tool()(clone_release_report)
server.tool()(create_page_from_adf)


@server.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


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


if __name__ == "__main__":
    settings = NetraSettings()  # type: ignore[call-arg]  # fields read from env
    _configure_logging(json_logs=settings.json_logs, log_level=settings.log_level)
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
