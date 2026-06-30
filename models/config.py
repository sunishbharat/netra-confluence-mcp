from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class NetraSettings(BaseSettings):
    confluence_base_url: str = Field(..., description="e.g. https://your-org.atlassian.net")
    confluence_api_token: str = Field(..., description="Confluence API token")
    confluence_user_email: str = Field(..., description="Service account email for Basic auth")
    confluence_site_url: str = Field(..., description="Used to build page URLs in responses")
    log_level: str = Field(default="INFO", description="structlog level")
    json_logs: bool = Field(
        default=False, description="Emit JSON log lines (production/CF) instead of console format"
    )
    server_transport: Literal["stdio", "streamable-http"] = Field(
        default="stdio",
        description="stdio for local MCP clients (Claude Desktop, CLI); "
        "streamable-http for CF/HTTP deployment",
    )
    server_host: str = Field(default="127.0.0.1", description="Bind host for streamable-http")
    server_port: int = Field(default=8765, description="Bind port for streamable-http")

    model_config = {"env_file": ".env", "extra": "forbid"}
