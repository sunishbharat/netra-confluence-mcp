from __future__ import annotations

from typing import Any

import structlog

from exceptions import NetraJiraError
from models.jira import validate_summary
from netra_jira.api import get_edit_fields, get_issue, update_issue
from netra_jira.fields import compute_field_changes, plain_text_to_adf, resolve_field_keys
from netra_jira.tools.shared import get_jira_client, validation_failed

log = structlog.get_logger()


async def edit_jira_issue(
    issue_key: str,
    summary: str | None = None,
    description: str | None = None,
    field_overrides: dict[str, Any] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Edit fields on one existing Jira issue. Only the fields passed are changed.

    summary: new summary. description: new plain-text description (replaces the
    existing one). field_overrides maps a field ID or exact field name to a value in
    Jira API shape (e.g. {"labels": ["a", "b"]} replaces all labels).

    Defaults to dry_run=True: returns a before/after entry for every field that would
    change, without writing. Show it to the user, then call again with dry_run=False.
    Status: DRY_RUN, UPDATED, NO_CHANGES, VALIDATION_FAILED, ERROR.
    """
    overrides = field_overrides or {}
    errors: list[str] = []
    new_summary: str | None = None
    if summary is not None:
        try:
            new_summary = validate_summary(summary)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return validation_failed(errors)

    try:
        async with get_jira_client() as client:
            issue = await get_issue(client, issue_key)
            edit_fields = await get_edit_fields(client, issue_key)

            resolved, errors = resolve_field_keys(overrides, edit_fields)
            if description is not None and "description" in resolved:
                errors.append("description given both as argument and in field_overrides")
            if errors:
                return validation_failed(errors)

            new_fields: dict[str, Any] = dict(resolved)
            if new_summary is not None:
                new_fields["summary"] = new_summary
            if description is not None:
                new_fields["description"] = plain_text_to_adf(description)

            changes = compute_field_changes(issue.fields, new_fields)
            if not changes:
                return {"status": "NO_CHANGES", "issue_key": issue.key}

            changed_ids = {c["field"] for c in changes}
            to_write = {k: v for k, v in new_fields.items() if k in changed_ids}

            if dry_run:
                return {
                    "status": "DRY_RUN",
                    "issue_key": issue.key,
                    "current_summary": issue.summary,
                    "changes": changes,
                    "message": "Preview only. Call again with dry_run=False to apply.",
                }

            await update_issue(client, issue.key, to_write)
            log.info("jira_issue_updated", issue_key=issue.key, fields=sorted(to_write))
            return {
                "status": "UPDATED",
                "issue_key": issue.key,
                "url": client.browse_url(issue.key),
                "fields_updated": sorted(to_write),
                "changes": changes,
            }
    except NetraJiraError as e:
        log.error("edit_jira_issue failed", issue_key=issue_key, error=str(e))
        return {"status": "ERROR", "error": str(e)}
