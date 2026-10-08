from __future__ import annotations

from typing import Any

import structlog

from exceptions import NetraJiraError
from netra_jira.api import get_issue, get_issue_comments
from netra_jira.display import describe_issue
from netra_jira.tools.shared import get_jira_client

log = structlog.get_logger()

# Most recent comments returned with include_comments=True: enough to see the open
# questions and decisions on a ticket without a long thread flooding the client's context.
_MAX_COMMENTS = 20


async def get_jira_issue(issue_key: str, include_comments: bool = False) -> dict[str, Any]:
    """
    Read one Jira issue in a readable form. Always read-only.

    Returns summary, issue type, issue_status, priority, assignee, reporter, labels,
    components, fix/affects versions, dates, description and environment as plain text,
    parent, sub-tasks, linked issues (with the relation, e.g. "is blocked by"),
    attachment file names, and every non-empty custom field keyed by its display name
    (e.g. "Acceptance Criteria", "Story Points").

    include_comments=True adds the 20 most recent comments, newest first, plus
    comments_total. Pass it when the user asks to analyse, review, or give feedback on
    a ticket, since comments hold open questions and decisions.

    Attachment contents are not read. Status: INSPECTION, ERROR.
    """
    try:
        async with get_jira_client() as client:
            issue = await get_issue(client, issue_key, with_names=True)
            result: dict[str, Any] = {
                "status": "INSPECTION",
                **describe_issue(issue, client.browse_url(issue.key)),
            }
            if include_comments:
                page = await get_issue_comments(client, issue.key, _MAX_COMMENTS)
                result["comments"] = [c.model_dump() for c in page.comments]
                result["comments_total"] = page.total
            return result
    except NetraJiraError as e:
        log.error("get_jira_issue failed", issue_key=issue_key, error=str(e))
        return {"status": "ERROR", "error": str(e)}
