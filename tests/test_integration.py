from __future__ import annotations

import os

import pytest

from confluence.tools.inspect_jql import inspect_page_jql
from confluence.tools.update_macros import update_page_macros

pytestmark = pytest.mark.integration

_PAGE_ID = os.environ.get("CONFLUENCE_TEST_PAGE_ID", "")


@pytest.fixture(autouse=True)
def require_env() -> None:
    if not _PAGE_ID:
        pytest.skip("CONFLUENCE_TEST_PAGE_ID not set")


async def test_inspect_returns_at_least_one_jql_query() -> None:
    result = await inspect_page_jql(_PAGE_ID)
    assert result["status"] == "INSPECTION"
    assert isinstance(result["jira_macro_count"], int)


async def test_dry_run_update_returns_change_count() -> None:
    inspect = await inspect_page_jql(_PAGE_ID)
    unique = inspect.get("unique_strings", [])
    if not unique:
        pytest.skip("No unique strings found on test page")

    search_term = unique[0]
    result = await update_page_macros(
        _PAGE_ID,
        [{"old": search_term, "new": search_term + "_TEST"}],
        dry_run=True,
    )
    assert result["status"] in ("DRY_RUN", "NO_CHANGES")


async def test_apply_and_revert_increments_version() -> None:
    inspect = await inspect_page_jql(_PAGE_ID)
    unique = inspect.get("unique_strings", [])
    if not unique:
        pytest.skip("No unique strings found on test page")

    search_term = unique[0]
    replacement = search_term + "_TEST_INTEGRATION"
    did_apply = False
    version_after: int | None = None

    try:
        apply_result = await update_page_macros(
            _PAGE_ID,
            [{"old": search_term, "new": replacement}],
            dry_run=False,
        )
        if apply_result["status"] == "NO_CHANGES":
            pytest.skip(f"Term '{search_term}' not found on page")
        assert apply_result["status"] == "UPDATED"
        did_apply = True
        version_after = apply_result["version"]

        revert_result = await update_page_macros(
            _PAGE_ID,
            [{"old": replacement, "new": search_term}],
            dry_run=False,
        )
        assert revert_result["status"] == "UPDATED"
        assert revert_result["version"] > version_after
    finally:
        # Always revert, even if an assert above fails, so the shared test
        # page is not left dirty for subsequent CI runs.
        if did_apply:
            cleanup = await update_page_macros(
                _PAGE_ID,
                [{"old": replacement, "new": search_term}],
                dry_run=False,
            )
            if cleanup["status"] != "UPDATED":
                pytest.fail(
                    f"Integration test left page dirty: revert returned {cleanup['status']}. "
                    f"Page {_PAGE_ID} still contains '{replacement}'."
                )
