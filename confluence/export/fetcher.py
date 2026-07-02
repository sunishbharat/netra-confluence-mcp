from __future__ import annotations

from dataclasses import dataclass

from confluence.client import ConfluenceClient
from exceptions import ConfluenceAPIError, PageTooLargeError
from models.config import NetraSettings


@dataclass(frozen=True)
class ExportPageData:
    page_id: str
    title: str
    version: int
    space_key: str
    html: str


async def fetch_export_view(
    client: ConfluenceClient, page_id: str, settings: NetraSettings
) -> ExportPageData:
    """Fetch a page's export_view HTML and provenance metadata.

    v1 API only - v2 has no export_view representation. Reuses the
    per-request ConfluenceClient, so this runs with the caller's identity:
    a page the caller cannot view raises ConfluencePermissionError exactly
    like the write tools.
    """
    response = await client.get(
        f"/wiki/rest/api/content/{page_id}",
        params={"expand": "body.export_view,version,space,history.lastUpdated"},
    )
    data: dict[str, object] = response.json()

    body = data.get("body")
    if not isinstance(body, dict):
        raise ConfluenceAPIError(f"Confluence response for page {page_id} is missing 'body'")
    export_view = body.get("export_view")
    if not isinstance(export_view, dict) or "value" not in export_view:
        raise ConfluenceAPIError(
            f"Confluence response for page {page_id} is missing 'body.export_view.value' "
            "(export_view representation not available for this content type)"
        )
    html = str(export_view["value"])

    measured = len(html.encode("utf-8"))
    if measured > settings.export_max_html_bytes:
        raise PageTooLargeError(measured=measured, cap=settings.export_max_html_bytes)

    version = data.get("version")
    if not isinstance(version, dict) or "number" not in version:
        raise ConfluenceAPIError(
            f"Confluence response for page {page_id} is missing 'version.number'"
        )

    if "id" not in data or "title" not in data:
        raise ConfluenceAPIError(
            f"Confluence response for page {page_id} is missing 'id' or 'title'"
        )

    space = data.get("space")
    space_key = str(space["key"]) if isinstance(space, dict) and "key" in space else ""

    return ExportPageData(
        page_id=str(data["id"]),
        title=str(data["title"]),
        # object -> str -> int: mypy --strict rejects int(object) directly.
        version=int(str(version["number"])),
        space_key=space_key,
        html=html,
    )
