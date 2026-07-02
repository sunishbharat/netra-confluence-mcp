from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import confluence.tools.inspect_jql as module
from exceptions import PageNotFoundError
from models.confluence import PageContent

_ADF_WITH_JQL: dict[str, Any] = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "extension",
            "attrs": {
                "extensionType": "com.atlassian.confluence.macro.core",
                "extensionKey": "jira",
                "parameters": {
                    "macroParams": {
                        "jqlQuery": {"value": "project = TEST AND fixVersion = R1.0"},
                        "columns": {"value": "key,summary"},
                    },
                    "macroMetadata": {
                        "macroId": {"value": "test-macro-id-1234"},
                    },
                },
            },
        }
    ],
}

_ADF_EMPTY: dict[str, Any] = {"type": "doc", "version": 1, "content": []}


def _make_page(adf: dict[str, Any]) -> PageContent:
    return PageContent(
        id="123456",
        title="Test Page",
        version=5,
        space_id="SPACE001",
        status="current",
        adf=adf,
    )


@pytest.fixture(autouse=True)
def patch_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    fake.site_url = "https://test.atlassian.net"
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(module, "get_client", lambda: fake)
    return fake


async def test_inspect_page_jql_returns_inspection_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page(_ADF_WITH_JQL)))
    result = await module.inspect_page_jql("123456")
    assert result["status"] == "INSPECTION"


async def test_inspect_page_jql_returns_page_id_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page(_ADF_WITH_JQL)))
    result = await module.inspect_page_jql("123456")
    assert result["page_id"] == "123456"
    assert result["title"] == "Test Page"


async def test_inspect_page_jql_extracts_jql_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page(_ADF_WITH_JQL)))
    result = await module.inspect_page_jql("123456")
    queries = result["jql_queries"]
    assert isinstance(queries, list)
    assert len(queries) == 1
    assert queries[0]["jql"] == "project = TEST AND fixVersion = R1.0"


async def test_inspect_page_jql_macro_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page(_ADF_WITH_JQL)))
    result = await module.inspect_page_jql("123456")
    assert result["jira_macro_count"] == 1


async def test_inspect_page_jql_unique_strings_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page(_ADF_WITH_JQL)))
    result = await module.inspect_page_jql("123456")
    unique = result["unique_strings"]
    assert isinstance(unique, list)
    assert "R1.0" in unique


async def test_inspect_page_jql_empty_page_returns_zero_macros(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page(_ADF_EMPTY)))
    result = await module.inspect_page_jql("123456")
    assert result["status"] == "INSPECTION"
    assert result["jira_macro_count"] == 0
    assert result["jql_queries"] == []


async def test_inspect_page_jql_error_returns_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module, "read_page", AsyncMock(side_effect=PageNotFoundError("Page not found"))
    )
    result = await module.inspect_page_jql("999")
    assert result["status"] == "ERROR"
    assert "Page not found" in str(result["error"])
