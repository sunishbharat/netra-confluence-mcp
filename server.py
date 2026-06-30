from __future__ import annotations

import logging

import structlog
from mcp.server.fastmcp import FastMCP
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


def _configure_logging(*, json_logs: bool, log_level: str) -> None:
    """structlog: console output for local dev, JSON lines for the CF log drain."""
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)
    renderer = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


if __name__ == "__main__":
    settings = NetraSettings()  # type: ignore[call-arg]  # fields read from env
    _configure_logging(json_logs=settings.json_logs, log_level=settings.log_level)
    server.settings.host = settings.server_host
    server.settings.port = settings.server_port
    # Every tool call is an independent read-transform-write against Confluence - there is
    # no per-session state to keep, so streamable-http always runs stateless (no session
    # affinity required, safe to scale horizontally behind the CF router).
    server.settings.stateless_http = settings.server_transport == "streamable-http"
    logger.info(
        "server_starting",
        transport=settings.server_transport,
        host=settings.server_host,
        port=settings.server_port,
        stateless_http=server.settings.stateless_http,
    )
    server.run(transport=settings.server_transport)
