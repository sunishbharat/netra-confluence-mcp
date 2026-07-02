from __future__ import annotations

from typing import Any

import structlog

from confluence.adf.inspector import AdfInspector
from confluence.api import read_page
from confluence.tools.shared import get_client
from exceptions import NetraConfluenceError

log = structlog.get_logger()


async def inspect_page_jql(page_id: str) -> dict[str, Any]:
    """
    Read any Confluence page and return all Jira macro JQL queries
    with their parameters, ADF location paths, and a list of
    unique value tokens - the candidates for replacement.

    Always read-only. Use this before update_page_macros to understand
    what JQL exists on the page and identify what to replace.
    """
    try:
        async with get_client() as client:
            page = await read_page(client, page_id)
            inspection = AdfInspector.build_inspection(page.id, page.title, page.adf)
            return {"status": "INSPECTION", **inspection.model_dump()}
    except NetraConfluenceError as e:
        log.error("inspect_page_jql failed", page_id=page_id, error=str(e))
        return {"status": "ERROR", "error": str(e)}
