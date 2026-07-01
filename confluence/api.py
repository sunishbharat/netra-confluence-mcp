from __future__ import annotations

import json

from confluence.client import ConfluenceClient
from models.confluence import (
    CreatePageRequest,
    PageContent,
    PageMetadata,
    UpdatePageRequest,
)


async def read_page(client: ConfluenceClient, page_id: str) -> PageContent:
    response = await client.get(
        f"/wiki/api/v2/pages/{page_id}",
        params={"body-format": "atlas_doc_format"},
    )
    data: dict[str, object] = response.json()
    body = data["body"]
    assert isinstance(body, dict)
    atlas = body["atlas_doc_format"]
    assert isinstance(atlas, dict)
    adf_value = atlas["value"]
    adf: dict[str, object] = json.loads(adf_value) if isinstance(adf_value, str) else adf_value
    version = data["version"]
    assert isinstance(version, dict)
    return PageContent(
        id=str(data["id"]),
        title=str(data["title"]),
        # object -> str -> int: mypy --strict rejects int(object) directly.
        version=int(str(version["number"])),
        space_id=str(data["spaceId"]),
        status=str(data["status"]),
        adf=adf,
    )


async def get_page_metadata(client: ConfluenceClient, page_id: str) -> PageMetadata:
    response = await client.get(f"/wiki/api/v2/pages/{page_id}")
    data: dict[str, object] = response.json()
    version = data["version"]
    assert isinstance(version, dict)
    return PageMetadata(
        id=str(data["id"]),
        title=str(data["title"]),
        # object -> str -> int: mypy --strict rejects int(object) directly.
        version=int(str(version["number"])),
        space_id=str(data["spaceId"]),
        status=str(data["status"]),
    )


async def update_page(client: ConfluenceClient, request: UpdatePageRequest) -> PageMetadata:
    payload = {
        "id": request.page_id,
        "status": request.status,
        "title": request.title,
        "body": {
            "representation": "atlas_doc_format",
            "value": json.dumps(request.adf_body),
        },
        "version": {
            "number": request.version_number,
            "message": request.version_message,
        },
    }
    response = await client.put(f"/wiki/api/v2/pages/{request.page_id}", json=payload)
    data: dict[str, object] = response.json()
    version = data["version"]
    assert isinstance(version, dict)
    return PageMetadata(
        id=str(data["id"]),
        title=str(data["title"]),
        # object -> str -> int: mypy --strict rejects int(object) directly.
        version=int(str(version["number"])),
        space_id=str(data["spaceId"]),
        status=str(data["status"]),
    )


async def create_page(client: ConfluenceClient, request: CreatePageRequest) -> PageMetadata:
    payload: dict[str, object] = {
        "spaceId": request.space_id,
        "status": request.status,
        "title": request.title,
        "body": {
            "representation": "atlas_doc_format",
            "value": json.dumps(request.adf_body),
        },
    }
    if request.parent_id is not None:
        payload["parentId"] = request.parent_id
    response = await client.post("/wiki/api/v2/pages", json=payload)
    data: dict[str, object] = response.json()
    version = data["version"]
    assert isinstance(version, dict)
    return PageMetadata(
        id=str(data["id"]),
        title=str(data["title"]),
        # object -> str -> int: mypy --strict rejects int(object) directly.
        version=int(str(version["number"])),
        space_id=str(data["spaceId"]),
        status=str(data["status"]),
    )
