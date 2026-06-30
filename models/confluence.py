from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PageMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Confluence page ID")
    title: str = Field(..., description="Page title")
    version: int = Field(..., description="Current version number")
    space_id: str = Field(..., description="Space ID (not key)")
    status: str = Field(..., description="'current' or 'draft'")


class PageContent(PageMetadata):
    adf: dict = Field(..., description="Full ADF document node")  # type: ignore[type-arg]


class UpdatePageRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page_id: str = Field(..., description="Target page ID")
    title: str = Field(..., description="New page title")
    adf_body: dict = Field(..., description="ADF document node")  # type: ignore[type-arg]
    version_number: int = Field(..., description="Must be current version + 1")
    version_message: str = Field(
        default="Automated update via Netra MCP",
        description="Confluence version history message",
    )
    status: str = Field(default="current", description="'current' or 'draft'")


class CreatePageRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    space_id: str = Field(..., description="Target space ID")
    title: str = Field(..., description="Page title")
    adf_body: dict = Field(..., description="ADF document node")  # type: ignore[type-arg]
    parent_id: str | None = Field(default=None, description="Parent page ID")
    status: str = Field(default="current", description="'current' or 'draft'")
