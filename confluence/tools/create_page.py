from __future__ import annotations

from typing import Any

import structlog

from confluence.adf.validator import AdfValidator
from confluence.api import create_page
from confluence.client import ConfluenceClient
from confluence.tools.shared import get_client
from exceptions import ConfluenceAPIError, NetraConfluenceError
from models.confluence import CreatePageRequest

log = structlog.get_logger()


async def _resolve_space_id(client: ConfluenceClient, space_key: str) -> str:
    """Resolve a space key to its numeric space ID via the v2 API."""
    response = await client.get(
        "/wiki/api/v2/spaces",
        params={"keys": space_key, "limit": 1},
    )
    data: dict[str, object] = response.json()
    results = data.get("results", [])
    if not isinstance(results, list) or not results:
        raise ConfluenceAPIError(f"Space not found for key '{space_key}'")
    first = results[0]
    if not isinstance(first, dict):
        raise ConfluenceAPIError(f"Unexpected space result for key '{space_key}'")
    return str(first["id"])


async def create_page_from_adf(
    space_key: str,
    title: str,
    adf_body: dict[str, object],
    parent_page_id: str | None = None,
    status: str = "current",
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Create a brand-new Confluence page from an ADF document.
    Resolves space_key to space_id, validates ADF, then creates.
    Defaults to dry_run=True. Pass dry_run=False to apply.
    """
    try:
        errors = AdfValidator.validate(adf_body)
        if errors:
            return {"status": "VALIDATION_FAILED", "errors": errors}

        if dry_run:
            return {
                "status": "DRY_RUN",
                "title": title,
                "space_key": space_key,
                "message": "Preview only. Call again with dry_run=False to create.",
            }

        async with get_client() as client:
            space_id = await _resolve_space_id(client, space_key)
            meta = await create_page(
                client,
                CreatePageRequest(
                    space_id=space_id,
                    title=title,
                    adf_body=adf_body,
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
            }
    except NetraConfluenceError as e:
        log.error("create_page_from_adf failed", space_key=space_key, title=title, error=str(e))
        return {"status": "ERROR", "error": str(e)}
