from __future__ import annotations

from typing import Any

import structlog

from confluence.adf.differ import AdfDiffer
from confluence.adf.replacer import AdfReplacer
from confluence.adf.validator import AdfValidator
from confluence.api import read_page
from confluence.tools.shared import get_client, safe_update, update_date_nodes
from exceptions import NetraConfluenceError, VersionConflictError
from models.adf import DryRunResult, ReplacementRule

log = structlog.get_logger()


async def update_release_version(
    page_id: str,
    replacements: list[dict[str, object]],
    new_delivery_date: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Replace release/version tokens throughout a Confluence page.
    Handles title, text nodes, JQL queries in Jira macros,
    column names, gadget parameters, and date nodes.

    Use update_page_macros for generic pages.
    Use this tool when you also need to update a date node timestamp.
    """
    try:
        rules = [ReplacementRule(**r) for r in replacements]  # type: ignore[arg-type]
        replacer = AdfReplacer(rules)

        client = get_client()
        page = await read_page(client, page_id)

        preview_adf, preview_title, preview_log = replacer.apply(page.adf, page.title)
        if new_delivery_date:
            preview_adf, date_log = update_date_nodes(preview_adf, new_delivery_date)
            preview_log = preview_log + date_log

        if not preview_log:
            return {"status": "NO_CHANGES", "page_id": page_id, "title": page.title}

        errors = AdfValidator.validate(preview_adf)
        if errors:
            return {"status": "VALIDATION_FAILED", "errors": errors}

        change_summary = AdfDiffer.summarize_changes(preview_log)

        if dry_run:
            result = DryRunResult(
                current_title=page.title,
                new_title=preview_title,
                current_version=page.version,
                total_changes=len(preview_log),
                change_summary=change_summary,
                change_log=preview_log,
            )
            return result.model_dump()

        def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
            new_adf, new_title, change_log = replacer.apply(adf, title)
            if new_delivery_date:
                new_adf, date_log = update_date_nodes(new_adf, new_delivery_date)
                change_log = change_log + date_log
            return new_adf, new_title, change_log

        meta, applied_log = await safe_update(
            client, page_id, transform, "Release version update via Netra MCP"
        )
        return {
            "status": "UPDATED",
            "page_id": meta.id,
            "title": meta.title,
            "version": meta.version,
            "url": f"{client.site_url}/wiki/spaces/{meta.space_id}/pages/{meta.id}",
            "total_changes": len(applied_log),
            "change_summary": AdfDiffer.summarize_changes(applied_log),
        }
    except VersionConflictError:
        log.error("update_release_version exhausted retries on version conflict", page_id=page_id)
        return {"status": "VERSION_CONFLICT", "page_id": page_id}
    except NetraConfluenceError as e:
        log.error("update_release_version failed", page_id=page_id, error=str(e))
        return {"status": "ERROR", "error": str(e)}
