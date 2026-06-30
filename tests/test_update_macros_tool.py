from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import confluence.tools.update_macros as module
from exceptions import PageNotFoundError, VersionConflictError
from models.confluence import PageContent, PageMetadata

_ADF: dict[str, Any] = {
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
                        "macroId": {"value": "macro-id-001"},
                    },
                },
            },
        }
    ],
}

_ADF_NO_MATCH: dict[str, Any] = {"type": "doc", "version": 1, "content": []}


def _make_page(adf: dict[str, Any] = _ADF, title: str = "R1.0 Report") -> PageContent:
    return PageContent(
        id="123456", title=title, version=5, space_id="SPACE001", status="current", adf=adf
    )


def _make_meta(version: int = 6) -> PageMetadata:
    return PageMetadata(
        id="123456", title="R2.0 Report", version=version, space_id="SPACE001", status="current"
    )


@pytest.fixture(autouse=True)
def patch_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    fake.site_url = "https://test.atlassian.net"
    monkeypatch.setattr(module, "get_client", lambda: fake)
    return fake


async def test_dry_run_returns_dry_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.update_page_macros(
        "123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=True
    )
    assert result["status"] == "DRY_RUN"


async def test_dry_run_reports_change_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.update_page_macros(
        "123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=True
    )
    assert isinstance(result["total_changes"], int)
    assert result["total_changes"] > 0


async def test_dry_run_does_not_call_safe_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    mock_safe_update = AsyncMock()
    monkeypatch.setattr(module, "safe_update", mock_safe_update)
    await module.update_page_macros("123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=True)
    mock_safe_update.assert_not_called()


async def test_dry_run_includes_change_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.update_page_macros(
        "123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=True
    )
    assert isinstance(result["change_log"], list)
    assert len(result["change_log"]) > 0


async def test_apply_calls_safe_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    mock_safe_update = AsyncMock(return_value=(_make_meta(), []))
    monkeypatch.setattr(module, "safe_update", mock_safe_update)
    await module.update_page_macros("123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=False)
    mock_safe_update.assert_called_once()


async def test_apply_returns_updated_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    monkeypatch.setattr(module, "safe_update", AsyncMock(return_value=(_make_meta(), [])))
    result = await module.update_page_macros(
        "123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=False
    )
    assert result["status"] == "UPDATED"


async def test_apply_returns_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    monkeypatch.setattr(module, "safe_update", AsyncMock(return_value=(_make_meta(), [])))
    result = await module.update_page_macros(
        "123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=False
    )
    assert "url" in result
    assert "123456" in str(result["url"])


async def test_no_match_returns_no_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    page = PageContent(
        id="123456",
        title="No Match Report",
        version=5,
        space_id="SPACE001",
        status="current",
        adf=_ADF_NO_MATCH,
    )
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=page))
    result = await module.update_page_macros(
        "123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=True
    )
    assert result["status"] == "NO_CHANGES"


async def test_validation_error_returns_validation_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_adf: dict[str, Any] = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "extension",
                "attrs": {
                    "extensionKey": "jira",
                    "parameters": {
                        "macroParams": {"jqlQuery": {"value": "fixVersion = R1.0"}},
                        "macroMetadata": {"macroId": {"value": "x"}},
                    },
                },
            }
        ],
    }
    monkeypatch.setattr(
        module,
        "read_page",
        AsyncMock(return_value=_make_page(bad_adf, title="R1.0 Page")),
    )
    result = await module.update_page_macros(
        "123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=True
    )
    assert result["status"] == "VALIDATION_FAILED"
    assert isinstance(result["errors"], list)


async def test_confluence_error_returns_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module, "read_page", AsyncMock(side_effect=PageNotFoundError("Page not found"))
    )
    result = await module.update_page_macros("999", [{"old": "R1.0", "new": "R2.0"}], dry_run=True)
    assert result["status"] == "ERROR"
    assert "Page not found" in str(result["error"])


async def test_version_conflict_returns_version_conflict_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    monkeypatch.setattr(
        module,
        "safe_update",
        AsyncMock(side_effect=VersionConflictError("Version conflict")),
    )
    result = await module.update_page_macros(
        "123456", [{"old": "R1.0", "new": "R2.0"}], dry_run=False
    )
    assert result["status"] == "VERSION_CONFLICT"
    assert result["page_id"] == "123456"
