from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from exceptions import NetraJiraError
from models.jira import DEFAULT_SEARCH_RESULTS, MAX_SEARCH_RESULTS, IssueSearchQuery
from netra_jira.api import get_all_fields, search_issues
from netra_jira.display import SEARCH_FIELDS, search_row
from netra_jira.fields import resolve_field_ids
from netra_jira.tools.shared import get_jira_client, validation_failed

log = structlog.get_logger()


async def search_jira_issues(
    jql: str,
    max_results: int = DEFAULT_SEARCH_RESULTS,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """
    Find Jira issues with a JQL query. Always read-only.

    Each issue row has key, url, summary, issue_type, issue_status, assignee,
    priority, and updated. fields adds more columns per row, given as field IDs or
    exact field names (e.g. ["labels", "Service Name"]); added columns are keyed by
    display name.

    max_results: 1-100, default 50. If more issues match, truncated is true and the
    query should be narrowed. Text search on summary uses `summary ~ "words"` and is
    fuzzy; compare the returned summaries when an exact match matters.

    Use get_jira_issue to read one issue in full. Status: INSPECTION,
    VALIDATION_FAILED (unknown field in fields), ERROR (invalid input or JQL).
    """
    try:
        query = IssueSearchQuery(jql=jql, max_results=max_results, fields=fields or [])
    except ValidationError as e:
        log.error("search_jira_issues received invalid input", error=str(e))
        return {"status": "ERROR", "error": str(e)}

    try:
        async with get_jira_client() as client:
            extra_ids: list[str] = []
            names: dict[str, str] = {}
            if query.fields:
                all_fields = await get_all_fields(client)
                extra_ids, errors = resolve_field_ids(query.fields, all_fields)
                if errors:
                    return validation_failed(errors)
                names = {f.field_id: f.name for f in all_fields}

            requested = list(dict.fromkeys([*(f for f, _ in SEARCH_FIELDS), *extra_ids]))
            result = await search_issues(client, query.jql, requested, query.max_results)
            rows = [
                search_row(hit, client.browse_url(hit.key), extra_ids, names)
                for hit in result.issues
            ]
            response: dict[str, Any] = {
                "status": "INSPECTION",
                "jql": query.jql,
                "count": len(rows),
                "truncated": result.truncated,
                "issues": rows,
            }
            if result.truncated:
                message = f"More than {query.max_results} issues match. Narrow the JQL"
                if query.max_results < MAX_SEARCH_RESULTS:
                    message += f", or raise max_results (up to {MAX_SEARCH_RESULTS})"
                response["message"] = message + "."
            return response
    except NetraJiraError as e:
        log.error("search_jira_issues failed", jql=query.jql, error=str(e))
        return {"status": "ERROR", "error": str(e)}
