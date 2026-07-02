from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class NetraSettings(BaseSettings):
    confluence_base_url: str = Field(..., description="e.g. https://your-org.atlassian.net")
    confluence_api_token: str | None = Field(
        default=None,
        description="Service-account API token for stdio transport. Required when "
        "server_transport=stdio; on http transport the server owns no credentials - "
        "each request supplies its own via X-Confluence-Api-Token.",
    )
    confluence_user_email: str | None = Field(
        default=None,
        description="Service-account email for stdio transport's Basic auth. Required when "
        "server_transport=stdio; on http transport the server owns no credentials - "
        "each request supplies its own via X-Confluence-User-Email.",
    )
    confluence_site_url: str = Field(..., description="Used to build page URLs in responses")
    log_level: str = Field(default="INFO", description="structlog level")
    json_logs: bool = Field(
        default=False, description="Emit JSON log lines (production/CF) instead of console format"
    )
    server_transport: Literal["stdio", "http"] = Field(
        default="stdio",
        description="stdio for local MCP clients (Claude Desktop, CLI); "
        "http for CF/HTTP deployment (FastMCP's streamable HTTP transport)",
    )
    server_host: str = Field(default="127.0.0.1", description="Bind host for http transport")
    server_port: int = Field(default=8765, description="Bind port for http transport")

    model_config = {"env_file": ".env", "extra": "forbid"}

    @model_validator(mode="after")
    def _require_credentials_for_stdio(self) -> NetraSettings:
        # http transport gets per-request credentials from headers (Tier 1
        # passthrough) and must never fall back to a shared server identity,
        # so these fields are only mandatory for the stdio (per-user process)
        # case where there is no request to pull headers from.
        if self.server_transport == "stdio" and (
            not self.confluence_api_token or not self.confluence_user_email
        ):
            raise ValueError(
                "confluence_api_token and confluence_user_email are required "
                "when server_transport=stdio"
            )
        return self
