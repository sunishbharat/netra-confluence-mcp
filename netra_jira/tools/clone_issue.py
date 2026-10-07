from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from exceptions import NetraJiraError
from models.jira import CloneBatch
from netra_jira.api import get_create_fields, get_issue, get_project_issue_types
from netra_jira.fields import build_clone_fields, resolve_field_keys
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


async def clone_jira_issue(
    template_issue_key: str,
    items: list[dict[str, Any]],
    target_project_key: str | None = None,
    link_to_template: bool = True,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Clone a Jira template issue into one or more new issues, 1-50 per call.

    Every field on the template that the target project's create screen accepts is
    copied (custom fields, labels, components, priority, description, ...), except
    summary, assignee, reporter, attachments, links, and parent. Each new issue gets
    an "is cloned by" link from the template unless link_to_template=False.

    items: list of {"summary": str, "field_overrides": {...}}.
      summary is the exact final summary - if the user gave a pattern such as
      <project><variant><service name>, expand it into one concrete summary per item
      before calling. field_overrides maps a field ID or exact field name to a value
      in Jira create-API shape and replaces the template's value for that item.

    target_project_key defaults to the template's project. In another project,
    components/fixVersions/versions are not copied (listed in dropped_fields) unless
    overridden.

    Items whose summary already exists in the target project are skipped, so re-running
    a partially failed call only creates the missing issues.

    Defaults to dry_run=True: returns a preview of every item without creating
    anything. Show the preview to the user, then call again with dry_run=False.
    Status: DRY_RUN, CREATED, PARTIAL, NO_CHANGES, VALIDATION_FAILED, ERROR.
    """
    try:
        batch = CloneBatch(items=items)  # type: ignore[arg-type]  # dicts parsed by pydantic
    except ValidationError as e:
        log.error(
            "clone_jira_issue received invalid items", template=template_issue_key, error=str(e)
        )
        return {"status": "ERROR", "error": str(e)}

    try:
        async with get_jira_client() as client:
            template = await get_issue(client, template_issue_key)
            target = target_project_key or template.project_key

            issue_types = await get_project_issue_types(client, target)
            type_meta = find_issue_type(issue_types, template.issue_type_name)
            if type_meta is None:
                return validation_failed(
                    [
                        f"template issue type '{template.issue_type_name}' "
                        f"is not available in project {target}"
                    ]
                )
            create_fields = await get_create_fields(client, target, type_meta.id)

            copied, dropped = build_clone_fields(
                template.fields, create_fields, cross_project=target != template.project_key
            )

            prepared: list[PreparedItem] = []
            errors: list[str] = []
            for index, item in enumerate(batch.items):
                resolved, item_errors = resolve_field_keys(item.field_overrides, create_fields)
                errors.extend(f"item[{index}] '{item.summary}': {err}" for err in item_errors)
                fields: dict[str, Any] = {**copied, **resolved}
                fields["project"] = {"key": target}
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

            existing = await find_existing(client, target, prepared)
            context: dict[str, Any] = {
                "template_issue": template.key,
                "target_project": target,
                "issue_type": type_meta.name,
                "copied_fields": sorted(copied),
                "dropped_fields": dropped,
            }
            if dry_run:
                return build_bulk_dry_run(context, prepared, existing)
            return await execute_bulk_create(
                client,
                context,
                prepared,
                existing,
                link_template_key=template.key if link_to_template else None,
            )
    except NetraJiraError as e:
        log.error("clone_jira_issue failed", template=template_issue_key, error=str(e))
        return {"status": "ERROR", "error": str(e)}
