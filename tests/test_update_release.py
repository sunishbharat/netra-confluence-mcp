from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import confluence.tools.update_release as module
from exceptions import PageNotFoundError, VersionConflictError
from models.confluence import PageContent, PageMetadata

_TS_OLD = "1778025600000"

_ADF_WITH_DATE: dict[str, Any] = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "paragraph",
            "content": [
                {"type": "date", "attrs": {"timestamp": _TS_OLD}},
            ],
        },
        {
            "type": "extension",
            "attrs": {
                "extensionType": "com.atlassian.confluence.macro.core",
                "extensionKey": "jira",
                "parameters": {
                    "macroParams": {
                        "jqlQuery": {"value": "fixVersion = R1.0"},
                    },
                    "macroMetadata": {"macroId": {"value": "macro-id-001"}},
                },
            },
        },
    ],
}

_ADF_NO_DATE: dict[str, Any] = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "extension",
            "attrs": {
                "extensionType": "com.atlassian.confluence.macro.core",
                "extensionKey": "jira",
                "parameters": {
                    "macroParams": {"jqlQuery": {"value": "fixVersion = R1.0"}},
                    "macroMetadata": {"macroId": {"value": "macro-id-001"}},
                },
            },
        }
    ],
}


def _make_page(adf: dict[str, Any] = _ADF_WITH_DATE) -> PageContent:
    return PageContent(
        id="123456", title="R1.0 Report", version=5, space_id="SPACE001", status="current", adf=adf
    )


def _make_meta() -> PageMetadata:
    return PageMetadata(
        id="123456", title="R2.0 Report", version=6, space_id="SPACE001", status="current"
    )


@pytest.fixture(autouse=True)
def patch_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    fake.site_url = "https://test.atlassian.net"
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(module, "get_client", lambda: fake)
    return fake


async def test_dry_run_returns_dry_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.update_release_version(
        "123456",
        [{"old": "R1.0", "new": "R2.0"}],
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    assert result["status"] == "DRY_RUN"


async def test_date_nodes_included_in_change_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.update_release_version(
        "123456",
        [{"old": "R1.0", "new": "R2.0"}],
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    log_locations = [e["location"] for e in result["change_log"]]
    assert any("date" in loc for loc in log_locations)


async def test_no_date_update_when_not_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.update_release_version(
        "123456",
        [{"old": "R1.0", "new": "R2.0"}],
        new_delivery_date=None,
        dry_run=True,
    )
    log_locations = [e["location"] for e in result["change_log"]]
    assert not any("date" in loc for loc in log_locations)


async def test_apply_returns_updated_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    monkeypatch.setattr(module, "safe_update", AsyncMock(return_value=(_make_meta(), [])))
    result = await module.update_release_version(
        "123456",
        [{"old": "R1.0", "new": "R2.0"}],
        new_delivery_date="2026-09-15",
        dry_run=False,
    )
    assert result["status"] == "UPDATED"


async def test_apply_does_not_call_safe_update_when_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    mock_safe = AsyncMock()
    monkeypatch.setattr(module, "safe_update", mock_safe)
    await module.update_release_version(
        "123456",
        [{"old": "R1.0", "new": "R2.0"}],
        dry_run=True,
    )
    mock_safe.assert_not_called()


async def test_version_conflict_returns_version_conflict_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    monkeypatch.setattr(
        module,
        "safe_update",
        AsyncMock(side_effect=VersionConflictError("Version conflict")),
    )
    result = await module.update_release_version(
        "123456",
        [{"old": "R1.0", "new": "R2.0"}],
        new_delivery_date="2026-09-15",
        dry_run=False,
    )
    assert result["status"] == "VERSION_CONFLICT"
    assert result["page_id"] == "123456"


async def test_no_changes_returns_no_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    page = PageContent(
        id="123456",
        title="No Match Report",
        version=5,
        space_id="SPACE001",
        status="current",
        adf={"type": "doc", "version": 1, "content": []},
    )
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=page))
    result = await module.update_release_version(
        "123456",
        [{"old": "R1.0", "new": "R2.0"}],
        dry_run=True,
    )
    assert result["status"] == "NO_CHANGES"


async def test_error_returns_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(side_effect=PageNotFoundError("not found")))
    result = await module.update_release_version(
        "999", [{"old": "R1.0", "new": "R2.0"}], dry_run=True
    )
    assert result["status"] == "ERROR"


async def test_date_node_timestamp_updated_in_change_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.update_release_version(
        "123456",
        [{"old": "R1.0", "new": "R2.0"}],
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    date_entries = [e for e in result["change_log"] if "date" in e["location"]]
    assert len(date_entries) == 1
    assert _TS_OLD in date_entries[0]["detail"]
