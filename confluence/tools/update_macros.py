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
)
from exceptions import AdfValidationError, NetraConfluenceError, VersionConflictError
from models.adf import ReplacementRule

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
    except ValidationError as e:
        log.error("update_page_macros received invalid replacements", page_id=page_id, error=str(e))
        return {"status": "ERROR", "error": str(e)}

    replacer = AdfReplacer(rules)

    try:
        async with get_client() as client:
            if dry_run:
                page = await read_page(client, page_id)
                preview_adf, preview_title, preview_log = replacer.apply(page.adf, page.title)

                if not preview_log:
                    return build_no_changes_response(page_id, page.title)

                errors = AdfValidator.validate(preview_adf)
                if errors:
                    return {"status": "VALIDATION_FAILED", "errors": errors}

                return build_dry_run_response(page.title, preview_title, page.version, preview_log)

            def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
                return replacer.apply(adf, title)

            meta, applied_log = await safe_update(
                client, page_id, transform, "Macro parameter update via Netra MCP"
            )
            if meta is None:
                return build_no_changes_response(page_id)

            return build_updated_response(client, meta, applied_log)
    except VersionConflictError:
        log.error("update_page_macros exhausted retries on version conflict", page_id=page_id)
        return {"status": "VERSION_CONFLICT", "page_id": page_id}
    except AdfValidationError as e:
        log.error("update_page_macros validation failed on write", page_id=page_id, errors=e.errors)
        return {"status": "VALIDATION_FAILED", "errors": e.errors}
    except NetraConfluenceError as e:
        log.error("update_page_macros failed", page_id=page_id, error=str(e))
        return {"status": "ERROR", "error": str(e)}
