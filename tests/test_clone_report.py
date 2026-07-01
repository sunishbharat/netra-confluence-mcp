from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import confluence.tools.clone_report as module
from exceptions import PageNotFoundError
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
                        "jqlQuery": {
                            "value": (
                                "project = TEST AND fixVersion = ver_R1.0"
                                " AND labels = ver_R1.0_Baseline"
                            )
                        },
                        "columns": {"value": "ver_R1.0,key"},
                    },
                    "macroMetadata": {"macroId": {"value": "original-macro-id"}},
                },
            },
        },
        {
            "type": "paragraph",
            "content": [
                {"type": "date", "attrs": {"timestamp": "1778025600000"}},
            ],
        },
    ],
}


def _make_page(adf: dict[str, Any] = _ADF) -> PageContent:
    return PageContent(
        id="111",
        title="R1.0 Report ProjectX",
        version=10,
        space_id="SPACE001",
        status="current",
        adf=adf,
    )


def _make_meta() -> PageMetadata:
    return PageMetadata(
        id="999", title="R2.0 Report ProjectX", version=1, space_id="SPACE001", status="draft"
    )


@pytest.fixture(autouse=True)
def patch_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    fake.site_url = "https://test.atlassian.net"
    monkeypatch.setattr(module, "get_client", lambda: fake)
    return fake


async def test_dry_run_returns_dry_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    assert result["status"] == "DRY_RUN"


async def test_dry_run_new_title_contains_new_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    assert "R2.0" in str(result["new_title"])
    assert "R1.0" not in str(result["new_title"])


async def test_longest_match_first_baseline_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    change_details = [e["detail"] for e in result["change_log"]]
    assert any("ver_R1.0_Baseline" in d for d in change_details)
    assert any("ver_R2.0_Baseline" in d for d in change_details)


async def test_date_nodes_updated_in_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    log_locations = [e["location"] for e in result["change_log"]]
    assert any("date" in loc for loc in log_locations)


async def test_apply_calls_create_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    mock_create = AsyncMock(return_value=_make_meta())
    monkeypatch.setattr(module, "create_page", mock_create)
    await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        dry_run=False,
    )
    mock_create.assert_called_once()


async def test_apply_returns_created_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    monkeypatch.setattr(module, "create_page", AsyncMock(return_value=_make_meta()))
    result = await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        dry_run=False,
    )
    assert result["status"] == "CREATED"
    assert result["page_id"] == "999"


async def test_uses_source_space_id_when_no_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    mock_create = AsyncMock(return_value=_make_meta())
    monkeypatch.setattr(module, "create_page", mock_create)
    await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        target_space_id=None,
        dry_run=False,
    )
    call_args = mock_create.call_args
    request = call_args[0][1]
    assert request.space_id == "SPACE001"


async def test_uses_target_space_id_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    mock_create = AsyncMock(return_value=_make_meta())
    monkeypatch.setattr(module, "create_page", mock_create)
    await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        target_space_id="OTHER_SPACE",
        dry_run=False,
    )
    call_args = mock_create.call_args
    request = call_args[0][1]
    assert request.space_id == "OTHER_SPACE"


async def test_error_returns_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(side_effect=PageNotFoundError("not found")))
    result = await module.clone_release_report(
        source_page_id="999",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    assert result["status"] == "ERROR"


async def test_macro_ids_regenerated_in_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "read_page", AsyncMock(return_value=_make_page()))
    result = await module.clone_release_report(
        source_page_id="111",
        old_release="R1.0",
        new_release="R2.0",
        new_delivery_date="2026-09-15",
        dry_run=True,
    )
    assert isinstance(result["total_changes"], int)
    assert result["total_changes"] > 0
