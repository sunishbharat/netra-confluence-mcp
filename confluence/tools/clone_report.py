from __future__ import annotations

from typing import Any

import structlog

from confluence.adf.differ import AdfDiffer
from confluence.adf.replacer import AdfReplacer
from confluence.adf.validator import AdfValidator
from confluence.api import create_page, read_page
from confluence.tools.shared import get_client, update_date_nodes
from exceptions import NetraConfluenceError
from models.adf import DryRunResult, ReplacementRule
from models.confluence import CreatePageRequest

log = structlog.get_logger()


async def clone_release_report(
    source_page_id: str,
    old_release: str,
    new_release: str,
    new_delivery_date: str,
    target_space_id: str | None = None,
    parent_page_id: str | None = None,
    status: str = "draft",
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Clone a release report page and update all release tokens and macroIds.

    Applies replacements in longest-match order:
      1. ver_{old_release}_Baseline -> ver_{new_release}_Baseline
      2. ver_{old_release} -> ver_{new_release}
      3. {old_release} -> {new_release} (also regenerates all macroIds)
    """
    try:
        rules = [
            ReplacementRule(
                old=f"ver_{old_release}_Baseline",
                new=f"ver_{new_release}_Baseline",
                scope="all",
                regenerate_macro_ids=False,
            ),
            ReplacementRule(
                old=f"ver_{old_release}",
                new=f"ver_{new_release}",
                scope="all",
                regenerate_macro_ids=False,
            ),
            ReplacementRule(
                old=old_release,
                new=new_release,
                scope="all",
                regenerate_macro_ids=True,
            ),
        ]
        replacer = AdfReplacer(rules)

        async with get_client() as client:
            page = await read_page(client, source_page_id)

            space_id = target_space_id or page.space_id

            new_adf, new_title, change_log = replacer.apply(page.adf, page.title)
            new_adf, date_log = update_date_nodes(new_adf, new_delivery_date)
            all_change_log = change_log + date_log

            errors = AdfValidator.validate(new_adf)
            if errors:
                return {"status": "VALIDATION_FAILED", "errors": errors}

            change_summary = AdfDiffer.summarize_changes(all_change_log)

            if dry_run:
                result = DryRunResult(
                    current_title=page.title,
                    new_title=new_title,
                    current_version=page.version,
                    total_changes=len(all_change_log),
                    change_summary=change_summary,
                    change_log=all_change_log,
                )
                return result.model_dump()

            meta = await create_page(
                client,
                CreatePageRequest(
                    space_id=space_id,
                    title=new_title,
                    adf_body=new_adf,
                    parent_id=parent_page_id,
                    status=status,
                ),
            )
            return {
                "status": "CREATED",
                "page_id": meta.id,
                "title": meta.title,
                "version": meta.version,
                "url": f"{client.site_url}/wiki/spaces/{meta.space_id}/pages/{meta.id}",
                "total_changes": len(all_change_log),
                "change_summary": change_summary,
            }
    except NetraConfluenceError as e:
        log.error("clone_release_report failed", source_page_id=source_page_id, error=str(e))
        return {"status": "ERROR", "error": str(e)}
