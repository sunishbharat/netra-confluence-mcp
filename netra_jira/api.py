from __future__ import annotations

import asyncio
from typing import Any

import structlog

from exceptions import JiraAPIError
from models.jira import (
    BulkCreateResponse,
    CreatedIssue,
    FieldMeta,
    IssueTypeMeta,
    JiraIssue,
)
from netra_jira.client import JiraClient, describe_error_body
from netra_jira.fields import summary_search_jql

log = structlog.get_logger()

# Page size for createmeta listings; 50 is Jira's documented default and keeps each
# page small enough that pagination bugs show up in projects with many custom fields.
_META_PAGE_SIZE = 50

# Search page size and page cap for the duplicate check. A phrase search on one summary
# normally returns a handful of issues; the cap bounds the worst case (a very generic
# summary) at 5 x 50 = 250 candidates per item.
_SEARCH_PAGE_SIZE = 50
_MAX_SEARCH_PAGES = 5

# Concurrent duplicate-check searches per batch: parallel enough that a 50-item batch
# does not take 50 sequential round-trips, low enough to stay clear of rate limits.
_SEARCH_CONCURRENCY = 5

# Link type used for "clones" / "is cloned by". This is the name of Jira Cloud's
# built-in link type, not a display string.
_CLONE_LINK_TYPE = "Cloners"


async def get_issue(client: JiraClient, issue_key: str) -> JiraIssue:
    """GET /rest/api/3/issue/{key} with all fields (v3: descriptions are ADF)."""
    response = await client.get(f"/rest/api/3/issue/{issue_key}")
    data: dict[str, Any] = response.json()
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise JiraAPIError(f"Issue {issue_key} response has no fields object")
    project = fields.get("project") or {}
    issue_type = fields.get("issuetype") or {}
    return JiraIssue(
        id=str(data["id"]),
        key=str(data["key"]),
        project_key=str(project.get("key", "")),
        issue_type_name=str(issue_type.get("name", "")),
        summary=str(fields.get("summary") or ""),
        fields=fields,
    )


async def _paginate_meta(client: JiraClient, path: str, list_keys: tuple[str, ...]) -> list[Any]:
    """Collect all entries of a startAt/maxResults paginated createmeta listing."""
    entries: list[Any] = []
    start_at = 0
    while True:
        response = await client.get(
            path, params={"startAt": start_at, "maxResults": _META_PAGE_SIZE}
        )
        data: dict[str, Any] = response.json()
        page: list[Any] = []
        for key in list_keys:
            value = data.get(key)
            if isinstance(value, list):
                page = value
                break
        entries.extend(page)
        total = data.get("total")
        start_at += len(page)
        if not page or (isinstance(total, int) and start_at >= total):
            return entries


async def get_project_issue_types(client: JiraClient, project_key: str) -> list[IssueTypeMeta]:
    """Issue types that can be created in a project."""
    raw = await _paginate_meta(
        client,
        f"/rest/api/3/issue/createmeta/{project_key}/issuetypes",
        ("issueTypes", "values"),
    )
    return [IssueTypeMeta(id=str(t["id"]), name=str(t["name"])) for t in raw]


async def get_create_fields(
    client: JiraClient, project_key: str, issue_type_id: str
) -> list[FieldMeta]:
    """Fields on the create screen for a project + issue type."""
    raw = await _paginate_meta(
        client,
        f"/rest/api/3/issue/createmeta/{project_key}/issuetypes/{issue_type_id}",
        ("fields", "values"),
    )
    return [
        FieldMeta(
            field_id=str(f["fieldId"]),
            name=str(f.get("name", f["fieldId"])),
            required=bool(f.get("required", False)),
            has_default_value=bool(f.get("hasDefaultValue", False)),
        )
        for f in raw
    ]


async def get_edit_fields(client: JiraClient, issue_key: str) -> list[FieldMeta]:
    """Fields the caller may edit on an existing issue."""
    response = await client.get(f"/rest/api/3/issue/{issue_key}/editmeta")
    data: dict[str, Any] = response.json()
    fields = data.get("fields") or {}
    return [
        FieldMeta(
            field_id=str(field_id),
            name=str(meta.get("name", field_id)),
            required=bool(meta.get("required", False)),
            has_default_value=bool(meta.get("hasDefaultValue", False)),
        )
        for field_id, meta in fields.items()
        if isinstance(meta, dict)
    ]


async def _find_existing_summary(client: JiraClient, project_key: str, summary: str) -> str | None:
    jql = summary_search_jql(project_key, summary)
    if jql is None:
        log.warning("jira_duplicate_check_skipped", reason="summary has no searchable text")
        return None

    next_token: str | None = None
    for _ in range(_MAX_SEARCH_PAGES):
        body: dict[str, Any] = {
            "jql": jql,
            "fields": ["summary"],
            "maxResults": _SEARCH_PAGE_SIZE,
        }
        if next_token:
            body["nextPageToken"] = next_token
        # Search is a read even though it is a POST, so it may be retried on network errors.
        response = await client.post("/rest/api/3/search/jql", idempotent=True, json=body)
        data: dict[str, Any] = response.json()
        for issue in data.get("issues") or []:
            fields = issue.get("fields") or {}
            # `~` is a fuzzy text search; only an exact summary counts as a duplicate.
            if str(fields.get("summary", "")).strip() == summary:
                return str(issue["key"])
        next_token = data.get("nextPageToken")
        if not next_token:
            return None
    return None


async def find_existing_summaries(
    client: JiraClient, project_key: str, summaries: list[str]
) -> dict[str, str]:
    """Return {summary: existing_issue_key} for summaries already used in the project."""
    semaphore = asyncio.Semaphore(_SEARCH_CONCURRENCY)

    async def check(summary: str) -> tuple[str, str | None]:
        async with semaphore:
            return summary, await _find_existing_summary(client, project_key, summary)

    results = await asyncio.gather(*(check(s) for s in summaries))
    return {summary: key for summary, key in results if key is not None}


async def bulk_create_issues(
    client: JiraClient, field_sets: list[dict[str, Any]]
) -> BulkCreateResponse:
    """POST /rest/api/3/issue/bulk; map created issues and errors back to request indexes.

    Jira answers 201 when at least one issue was created and 400 when none were; both
    carry the same {issues, errors} body, so 400 is parsed rather than raised.
    """
    payload = {"issueUpdates": [{"fields": fields} for fields in field_sets]}
    response = await client.post(
        "/rest/api/3/issue/bulk", allow_status=frozenset({400}), json=payload
    )
    data: dict[str, Any] = response.json()
    raw_errors = data.get("errors")
    raw_issues = data.get("issues")
    if response.status_code == 400 and not isinstance(raw_errors, list):
        # A 400 without per-element errors is a malformed request, not a partial failure.
        raise JiraAPIError(f"Jira bulk create rejected (HTTP 400): {describe_error_body(data)}")

    errors: dict[int, str] = {}
    for err in raw_errors or []:
        index = err.get("failedElementNumber")
        if isinstance(index, int):
            errors[index] = describe_error_body(err.get("elementErrors") or err)

    issues = [i for i in (raw_issues or []) if isinstance(i, dict)]
    pending = [i for i in range(len(field_sets)) if i not in errors]
    if len(issues) != len(pending):
        log.error(
            "jira_bulk_create_count_mismatch",
            requested=len(field_sets),
            failed=len(errors),
            created=len(issues),
        )
    created = {
        index: CreatedIssue(id=str(issue["id"]), key=str(issue["key"]))
        for index, issue in zip(pending, issues, strict=False)
    }
    return BulkCreateResponse(created=created, errors=errors)


async def create_clone_link(client: JiraClient, new_issue_key: str, template_key: str) -> None:
    """Link a new issue to its template: new issue 'clones' template, template
    'is cloned by' new issue. Direction to be confirmed in the Phase 0 spike."""
    await client.post(
        "/rest/api/3/issueLink",
        json={
            "type": {"name": _CLONE_LINK_TYPE},
            "inwardIssue": {"key": new_issue_key},
            "outwardIssue": {"key": template_key},
        },
    )


async def update_issue(client: JiraClient, issue_key: str, fields: dict[str, Any]) -> None:
    """PUT /rest/api/3/issue/{key} with the given fields."""
    await client.put(f"/rest/api/3/issue/{issue_key}", json={"fields": fields})
