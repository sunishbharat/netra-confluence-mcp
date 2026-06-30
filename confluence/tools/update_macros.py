from __future__ import annotations

from typing import Any

import structlog

from confluence.adf.differ import AdfDiffer
from confluence.adf.replacer import AdfReplacer
from confluence.adf.validator import AdfValidator
from confluence.api import read_page
from confluence.tools.shared import get_client, safe_update
from exceptions import NetraConfluenceError, VersionConflictError
from models.adf import DryRunResult, ReplacementRule

log = structlog.get_logger()


async def update_page_macros(
    page_id: str,
    replacements: list[dict[str, object]],
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Replace values in macro parameters on any Confluence page.

    replacements: list of ReplacementRule dicts.
      Use scope="jql" to replace only inside jqlQuery values.
      Use scope="macro_params" to replace across all macro param values.

    Works on any Confluence page - no page-type assumptions.
    Defaults to dry_run=True. Pass dry_run=False to apply.
    """
    try:
        rules = [ReplacementRule(**r) for r in replacements]  # type: ignore[arg-type]
        replacer = AdfReplacer(rules)

        client = get_client()
        page = await read_page(client, page_id)

        preview_adf, preview_title, preview_log = replacer.apply(page.adf, page.title)

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
            return replacer.apply(adf, title)

        meta, applied_log = await safe_update(
            client, page_id, transform, "Macro parameter update via Netra MCP"
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
        log.error("update_page_macros exhausted retries on version conflict", page_id=page_id)
        return {"status": "VERSION_CONFLICT", "page_id": page_id}
    except NetraConfluenceError as e:
        log.error("update_page_macros failed", page_id=page_id, error=str(e))
        return {"status": "ERROR", "error": str(e)}
