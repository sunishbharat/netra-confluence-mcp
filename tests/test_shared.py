from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import tenacity

import confluence.tools.shared as module
from exceptions import VersionConflictError
from models.confluence import PageContent, PageMetadata

_PAGE_ID = "123456"


def _make_page(version: int) -> PageContent:
    return PageContent(
        id=_PAGE_ID,
        title="R1.0 Report",
        version=version,
        space_id="SPACE001",
        status="current",
        adf={"type": "doc", "version": 1, "content": []},
    )


def _make_meta(version: int) -> PageMetadata:
    return PageMetadata(
        id=_PAGE_ID,
        title="R2.0 Report",
        version=version,
        space_id="SPACE001",
        status="current",
    )


@pytest.fixture
def patch_wait_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the exponential backoff with a zero wait so tests run instantly."""
    # tenacity's @retry decorator attaches the retry policy to the wrapped
    # function at runtime; the type stub does not expose `.retry`.
    retry_obj = module.safe_update.retry  # type: ignore[attr-defined]
    monkeypatch.setattr(retry_obj, "wait", tenacity.wait_none())


@pytest.fixture
def fake_client() -> MagicMock:
    return MagicMock(site_url="https://test.atlassian.net")


async def test_safe_update_succeeds_first_try(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """No 409 -> safe_update returns after a single read + write."""
    read_mock = AsyncMock(return_value=_make_page(5))
    write_mock = AsyncMock(return_value=_make_meta(6))
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, []

    meta, log = await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert meta.version == 6
    assert log == []
    assert read_mock.call_count == 1
    assert write_mock.call_count == 1


async def test_safe_update_retries_on_409_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """Two 409s then a success -> 3 reads, 3 writes, return final result."""
    read_mock = AsyncMock(side_effect=[_make_page(5), _make_page(6), _make_page(7)])
    write_mock = AsyncMock(
        side_effect=[
            VersionConflictError("concurrent edit 1"),
            VersionConflictError("concurrent edit 2"),
            _make_meta(8),
        ]
    )
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, []

    meta, log = await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert meta.version == 8
    assert log == []
    assert read_mock.call_count == 3, "safe_update must re-read on every retry"
    assert write_mock.call_count == 3, "safe_update must retry writes on 409"


async def test_safe_update_raises_after_three_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """Three consecutive 409s -> VersionConflictError propagates after 3 attempts."""
    read_mock = AsyncMock(side_effect=[_make_page(5), _make_page(6), _make_page(7)])
    write_mock = AsyncMock(side_effect=VersionConflictError("always conflicts"))
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, []

    with pytest.raises(VersionConflictError):
        await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert read_mock.call_count == 3
    assert write_mock.call_count == 3


async def test_safe_update_returns_transform_log(
    monkeypatch: pytest.MonkeyPatch,
    patch_wait_none: None,  # noqa: ARG001
    fake_client: MagicMock,
) -> None:
    """The change_log produced by transform_fn is returned alongside the metadata."""
    from models.adf import ChangeLogEntry

    read_mock = AsyncMock(return_value=_make_page(5))
    write_mock = AsyncMock(return_value=_make_meta(6))
    monkeypatch.setattr(module, "read_page", read_mock)
    monkeypatch.setattr(module, "update_page", write_mock)

    entry = ChangeLogEntry(location="jql:content/0", detail="'R1.0' -> 'R2.0'")

    def transform(adf: dict[str, Any], title: str) -> tuple[dict[str, Any], str, list[Any]]:
        return adf, title, [entry]

    meta, log = await module.safe_update(fake_client, _PAGE_ID, transform, "test message")
    assert meta.version == 6
    assert log == [entry]


async def test_update_date_nodes_does_not_mutate_input() -> None:
    """update_date_nodes must deep-copy internally so the caller's ADF is safe."""
    adf: dict[str, Any] = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "date", "attrs": {"timestamp": "1778025600000"}},
        ],
    }
    import copy

    original = copy.deepcopy(adf)

    new_adf, log = module.update_date_nodes(adf, "2026-09-15")

    # Input is untouched.
    assert adf == original
    # Output is a different object with the new timestamp.
    assert new_adf is not adf
    assert new_adf["content"][0]["attrs"]["timestamp"] != "1778025600000"
    assert log, "expected a date change log entry"
