from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from exceptions import NetraJiraError
from models.jira import CreateBatch
from netra_jira.api import get_create_fields, get_project_issue_types
from netra_jira.fields import plain_text_to_adf, resolve_field_keys
from netra_jira.tools.shared import (
    PreparedItem,
    build_bulk_dry_run,
    execute_bulk_create,
    find_existing,
    find_issue_type,
    get_jira_client,
    required_field_errors,
    validation_failed,
)

log = structlog.get_logger()


async def create_jira_issue(
    project_key: str,
    issue_type: str,
    items: list[dict[str, Any]],
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Create one or more new Jira issues (no template) in a project, 1-50 per call.

    items: list of {"summary": str, "description": str | None, "fields": {...}}.
      summary is the exact final summary - if the user gave a pattern such as
      <project><variant><service name>, expand it into one concrete summary per item
      before calling. description is plain text. fields maps a field ID
      (e.g. "labels", "customfield_10010") or exact field name to a value in Jira
      create-API shape (e.g. {"priority": {"name": "High"}, "labels": ["a"]}).

    Items whose summary already exists in the project are skipped, so re-running a
    partially failed call only creates the missing issues.

    Defaults to dry_run=True: returns a preview of every item without creating
    anything. Show the preview to the user, then call again with dry_run=False.
    Status: DRY_RUN, CREATED, PARTIAL, NO_CHANGES, VALIDATION_FAILED, ERROR.
    """
    try:
        batch = CreateBatch(items=items)  # type: ignore[arg-type]  # dicts parsed by pydantic
    except ValidationError as e:
        log.error("create_jira_issue received invalid items", project_key=project_key, error=str(e))
        return {"status": "ERROR", "error": str(e)}

    try:
        async with get_jira_client() as client:
            issue_types = await get_project_issue_types(client, project_key)
            type_meta = find_issue_type(issue_types, issue_type)
            if type_meta is None:
                available = sorted(t.name for t in issue_types)
                return validation_failed(
                    [f"issue type '{issue_type}' not available in {project_key}: {available}"]
                )
            create_fields = await get_create_fields(client, project_key, type_meta.id)

            prepared: list[PreparedItem] = []
            errors: list[str] = []
            for index, item in enumerate(batch.items):
                resolved, item_errors = resolve_field_keys(item.fields, create_fields)
                if item.description is not None and "description" in resolved:
                    item_errors.append("description given both as argument and in fields")
                errors.extend(f"item[{index}] '{item.summary}': {err}" for err in item_errors)
                fields: dict[str, Any] = dict(resolved)
                if item.description is not None:
                    fields["description"] = plain_text_to_adf(item.description)
                fields["project"] = {"key": project_key}
                fields["issuetype"] = {"id": type_meta.id}
                fields["summary"] = item.summary
                prepared.append(
                    PreparedItem(
                        index=index,
                        summary=item.summary,
                        fields=fields,
                        overrides=sorted(resolved),
                    )
                )

            errors.extend(required_field_errors(prepared, create_fields))
            if errors:
                return validation_failed(errors)

            existing = await find_existing(client, project_key, prepared)
            context: dict[str, Any] = {
                "project_key": project_key,
                "issue_type": type_meta.name,
            }
            if dry_run:
                return build_bulk_dry_run(context, prepared, existing)
            return await execute_bulk_create(client, context, prepared, existing)
    except NetraJiraError as e:
        log.error("create_jira_issue failed", project_key=project_key, error=str(e))
        return {"status": "ERROR", "error": str(e)}
