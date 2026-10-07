from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Hard limit of POST /rest/api/3/issue/bulk - a larger batch is rejected, never split
# silently, so the human always approves exactly what one apply call will create.
MAX_BULK_ITEMS = 50

# Jira rejects summaries longer than 255 characters at create time; failing here keeps the
# whole batch out of a partial-failure state for an error we can detect up front.
MAX_SUMMARY_LENGTH = 255

ItemStatus = Literal["WILL_CREATE", "SKIPPED_EXISTS", "CREATED", "FAILED"]


def validate_summary(v: str) -> str:
    """Strip and check a summary against the rules Jira enforces at create time."""
    stripped = v.strip()
    if not stripped:
        raise ValueError("summary must not be empty")
    if len(stripped) > MAX_SUMMARY_LENGTH:
        raise ValueError(f"summary must be at most {MAX_SUMMARY_LENGTH} characters")
    if "\n" in stripped or "\r" in stripped:
        raise ValueError("summary must not contain line breaks")
    return stripped


def _check_unique_summaries(summaries: list[str]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for summary in summaries:
        if summary in seen and summary not in duplicates:
            duplicates.append(summary)
        seen.add(summary)
    if duplicates:
        raise ValueError(f"summaries must be unique within a batch; duplicated: {duplicates}")


class CloneItem(BaseModel):
    """One issue to create from a template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(..., description="Exact summary for the new issue")
    field_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Field ID or exact field name -> value in Jira create-API shape. "
        "Applied on top of the fields copied from the template.",
    )

    @field_validator("summary")
    @classmethod
    def summary_must_be_valid(cls, v: str) -> str:
        return validate_summary(v)


class CreateItem(BaseModel):
    """One issue to create without a template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(..., description="Exact summary for the new issue")
    description: str | None = Field(
        default=None, description="Plain text; wrapped into a minimal ADF document"
    )
    fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Field ID or exact field name -> value in Jira create-API shape",
    )

    @field_validator("summary")
    @classmethod
    def summary_must_be_valid(cls, v: str) -> str:
        return validate_summary(v)


class CloneBatch(BaseModel):
    """Validated input list for clone_jira_issue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[CloneItem] = Field(
        ..., min_length=1, max_length=MAX_BULK_ITEMS, description="Issues to create"
    )

    @model_validator(mode="after")
    def summaries_unique(self) -> CloneBatch:
        _check_unique_summaries([item.summary for item in self.items])
        return self


class CreateBatch(BaseModel):
    """Validated input list for create_jira_issue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[CreateItem] = Field(
        ..., min_length=1, max_length=MAX_BULK_ITEMS, description="Issues to create"
    )

    @model_validator(mode="after")
    def summaries_unique(self) -> CreateBatch:
        _check_unique_summaries([item.summary for item in self.items])
        return self


class JiraIssue(BaseModel):
    """An issue as read from GET /rest/api/3/issue/{key}."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Numeric issue ID")
    key: str = Field(..., description="Issue key, e.g. PROJ-124")
    project_key: str = Field(..., description="Key of the project the issue belongs to")
    issue_type_name: str = Field(..., description="Issue type name, e.g. Task")
    summary: str = Field(..., description="Current summary")
    fields: dict[str, Any] = Field(
        ..., description="Raw fields object from the v3 API (descriptions are ADF)"
    )


class IssueTypeMeta(BaseModel):
    """An issue type available for creation in a project."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Issue type ID")
    name: str = Field(..., description="Issue type name")


class FieldMeta(BaseModel):
    """One field on a create or edit screen (createmeta / editmeta)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(..., description="Field ID, e.g. labels or customfield_10010")
    name: str = Field(..., description="Display name, e.g. Service Name")
    required: bool = Field(default=False, description="Must be set on create")
    has_default_value: bool = Field(
        default=False, description="Jira fills a default when the field is omitted"
    )


class CreatedIssue(BaseModel):
    """One issue returned by a successful create."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Numeric issue ID")
    key: str = Field(..., description="Issue key")


class BulkCreateResponse(BaseModel):
    """Parsed POST /rest/api/3/issue/bulk response, keyed by request index."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    created: dict[int, CreatedIssue] = Field(
        default_factory=dict, description="Request index -> created issue"
    )
    errors: dict[int, str] = Field(
        default_factory=dict, description="Request index -> human-readable error"
    )


class BulkItemResult(BaseModel):
    """Per-item entry in a bulk tool response (dry run and apply)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(..., description="Position of the item in the caller's list")
    summary: str = Field(..., description="Summary of the item")
    status: ItemStatus = Field(..., description="Item-level outcome")
    overrides: list[str] | None = Field(
        default=None, description="Resolved field IDs overridden on this item (dry run)"
    )
    issue_key: str | None = Field(default=None, description="Key of the created issue")
    url: str | None = Field(default=None, description="Browse URL of the created issue")
    existing_issue_key: str | None = Field(
        default=None, description="Key of the existing issue with the same summary"
    )
    error: str | None = Field(default=None, description="Why the item failed")
    link_error: str | None = Field(
        default=None, description="Issue was created but the clone link failed"
    )
