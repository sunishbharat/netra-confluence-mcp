from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReplacementRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    old: str = Field(..., description="Exact string to find")
    new: str = Field(..., description="Replacement string")
    scope: Literal["all", "text", "macro_params", "jql", "title"] = Field(
        default="all",
        description=(
            "Which ADF locations to apply this replacement in. "
            "'jql' = only jqlQuery param values; "
            "'macro_params' = all macro param values including columns and gadget params; "
            "'text' = text nodes only; 'title' = page title only; 'all' = everywhere"
        ),
    )
    regenerate_macro_ids: bool = Field(
        default=False,
        description="Regenerate all macroId UUIDs after replacement (use for page clones only)",
    )

    @field_validator("old")
    @classmethod
    def old_must_not_be_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Replacement 'old' string must not be empty")
        return v


class JqlInfo(BaseModel):
    """One extracted Jira macro with its JQL and all macro parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    macro_id: str = Field(..., description="macroId UUID from ADF")
    location_path: str = Field(
        ..., description="Human-readable ADF path, e.g. 'table[2]/row[5]/cell[1]'"
    )
    jql: str = Field(..., description="Raw JQL string extracted from jqlQuery param")
    columns: str = Field(default="", description="columns param value")
    server: str = Field(default="", description="Jira server name param value")
    max_issues: str = Field(default="", description="maximumIssues param value")


class PageJqlInspection(BaseModel):
    """Result of inspecting a page for all Jira macro JQL queries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_id: str
    title: str
    jira_macro_count: int = Field(..., description="Total number of Jira extension nodes found")
    jql_queries: list[JqlInfo] = Field(..., description="All extracted JQL entries")
    unique_strings: list[str] = Field(
        ...,
        description=(
            "Deduplicated token candidates extracted from all JQL values. "
            "Use these to identify what to replace."
        ),
    )


class ChangeLogEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    location: str = Field(..., description="ADF path or location type")
    detail: str = Field(..., description="Human-readable description of the change")


class DryRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["DRY_RUN"] = "DRY_RUN"
    current_title: str
    new_title: str
    current_version: int
    total_changes: int
    change_summary: str
    change_log: list[ChangeLogEntry]
    message: str = "Preview only. Call again with dry_run=False to apply."
