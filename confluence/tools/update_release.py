from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from confluence.adf.replacer import AdfReplacer
from confluence.adf.validator import AdfValidator
from confluence.api import read_page
from confluence.tools.shared import (
    build_dry_run_response,
    build_no_changes_response,
    build_updated_response,
    get_client,
    safe_update,
    update_date_nodes,
)
from exceptions import AdfValidationError, NetraConfluenceError, VersionConflictError
from models.adf import ReplacementRule

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
    except ValidationError as e:
        log.error(
            "update_release_version received invalid replacements", page_id=page_id, error=str(e)
        )
        return {"status": "ERROR", "error": str(e)}

    replacer = AdfReplacer(rules)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        new_adf, new_title, change_log = replacer.apply(adf, title)
        if new_delivery_date:
            new_adf, date_log = update_date_nodes(new_adf, new_delivery_date)
            change_log = change_log + date_log
        return new_adf, new_title, change_log

    try:
        async with get_client() as client:
            if dry_run:
                page = await read_page(client, page_id)
                preview_adf, preview_title, preview_log = transform(page.adf, page.title)

                if not preview_log:
                    return build_no_changes_response(page_id, page.title)

                errors = AdfValidator.validate(preview_adf)
                if errors:
                    return {"status": "VALIDATION_FAILED", "errors": errors}

                return build_dry_run_response(page.title, preview_title, page.version, preview_log)

            meta, applied_log = await safe_update(
                client, page_id, transform, "Release version update via Netra MCP"
            )
            if meta is None:
                return build_no_changes_response(page_id)

            return build_updated_response(client, meta, applied_log)
    except VersionConflictError:
        log.error("update_release_version exhausted retries on version conflict", page_id=page_id)
        return {"status": "VERSION_CONFLICT", "page_id": page_id}
    except AdfValidationError as e:
        log.error(
            "update_release_version validation failed on write", page_id=page_id, errors=e.errors
        )
        return {"status": "VALIDATION_FAILED", "errors": e.errors}
    except NetraConfluenceError as e:
        log.error("update_release_version failed", page_id=page_id, error=str(e))
        return {"status": "ERROR", "error": str(e)}
