from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from confluence.api import create_page, get_page_metadata, read_page, update_page
from confluence.client import ConfluenceClient
from models.confluence import CreatePageRequest, UpdatePageRequest

BASE = "https://test.atlassian.net"

PAGE_RESPONSE = {
    "id": "123456",
    "title": "Test Page",
    "status": "current",
    "spaceId": "SPACE001",
    "version": {"number": 5},
}

ADF_DOC = {"type": "doc", "version": 1, "content": []}

PAGE_WITH_BODY = {
    **PAGE_RESPONSE,
    "body": {
        "atlas_doc_format": {
            "representation": "atlas_doc_format",
            "value": json.dumps(ADF_DOC),
        }
    },
}


async def test_read_page_returns_page_content(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages/123456?body-format=atlas_doc_format",
        json=PAGE_WITH_BODY,
    )
    page = await read_page(client, "123456")
    assert page.id == "123456"
    assert page.title == "Test Page"
    assert page.version == 5
    assert page.space_id == "SPACE001"
    assert page.status == "current"


async def test_read_page_parses_adf_json_string(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages/123456?body-format=atlas_doc_format",
        json=PAGE_WITH_BODY,
    )
    page = await read_page(client, "123456")
    assert page.adf == ADF_DOC


async def test_read_page_parses_adf_dict_value(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    page_dict_adf = {
        **PAGE_RESPONSE,
        "body": {
            "atlas_doc_format": {
                "representation": "atlas_doc_format",
                "value": ADF_DOC,
            }
        },
    }
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages/123456?body-format=atlas_doc_format",
        json=page_dict_adf,
    )
    page = await read_page(client, "123456")
    assert page.adf == ADF_DOC


async def test_get_page_metadata_returns_metadata(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages/123456",
        json=PAGE_RESPONSE,
    )
    meta = await get_page_metadata(client, "123456")
    assert meta.id == "123456"
    assert meta.title == "Test Page"
    assert meta.version == 5
    assert meta.space_id == "SPACE001"
    assert meta.status == "current"


async def test_update_page_sends_correct_body(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages/123456",
        method="PUT",
        json={**PAGE_RESPONSE, "version": {"number": 6}},
    )
    request = UpdatePageRequest(
        page_id="123456",
        title="Updated Title",
        adf_body=ADF_DOC,
        version_number=6,
    )
    await update_page(client, request)
    sent = httpx_mock.get_requests()[0]
    body = json.loads(sent.content)
    assert body["title"] == "Updated Title"
    assert body["version"]["number"] == 6
    assert body["body"]["representation"] == "atlas_doc_format"
    assert json.loads(body["body"]["value"]) == ADF_DOC


async def test_update_page_returns_metadata(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages/123456",
        method="PUT",
        json={**PAGE_RESPONSE, "version": {"number": 6}},
    )
    request = UpdatePageRequest(
        page_id="123456",
        title="Updated Title",
        adf_body=ADF_DOC,
        version_number=6,
    )
    meta = await update_page(client, request)
    assert meta.version == 6
    assert meta.title == "Test Page"


async def test_create_page_sends_correct_body(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages",
        method="POST",
        json={**PAGE_RESPONSE, "version": {"number": 1}},
    )
    request = CreatePageRequest(
        space_id="SPACE001",
        title="New Page",
        adf_body=ADF_DOC,
    )
    await create_page(client, request)
    sent = httpx_mock.get_requests()[0]
    body = json.loads(sent.content)
    assert body["spaceId"] == "SPACE001"
    assert body["title"] == "New Page"
    assert body["body"]["representation"] == "atlas_doc_format"
    assert "parentId" not in body


async def test_create_page_includes_parent_id_when_set(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages",
        method="POST",
        json={**PAGE_RESPONSE, "version": {"number": 1}},
    )
    request = CreatePageRequest(
        space_id="SPACE001",
        title="Child Page",
        adf_body=ADF_DOC,
        parent_id="999",
    )
    await create_page(client, request)
    sent = httpx_mock.get_requests()[0]
    body = json.loads(sent.content)
    assert body["parentId"] == "999"


async def test_create_page_returns_metadata(
    httpx_mock: HTTPXMock, client: ConfluenceClient
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/wiki/api/v2/pages",
        method="POST",
        json={**PAGE_RESPONSE, "version": {"number": 1}},
    )
    request = CreatePageRequest(
        space_id="SPACE001",
        title="New Page",
        adf_body=ADF_DOC,
    )
    meta = await create_page(client, request)
    assert meta.id == "123456"
    assert meta.version == 1
