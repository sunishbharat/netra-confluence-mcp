from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import netra_jira.tools.shared as module
from exceptions import JiraAPIError, MissingJiraCredentialsError
from models.jira import BulkCreateResponse, CreatedIssue

# --- credential resolution -------------------------------------------------------


def _set_base_env(monkeypatch: pytest.MonkeyPatch, transport: str) -> None:
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_SITE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("SERVER_TRANSPORT", transport)
    # Empty strings override any values in a local .env, so each test sees only
    # the credentials it sets explicitly.
    for name in (
        "JIRA_USER_EMAIL",
        "JIRA_API_TOKEN",
        "CONFLUENCE_USER_EMAIL",
        "CONFLUENCE_API_TOKEN",
    ):
        monkeypatch.setenv(name, "")


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace JiraClient with a recorder of the credentials it was built with."""
    seen: dict[str, Any] = {}

    def fake_client(**kwargs: Any) -> MagicMock:  # noqa: ANN401
        seen.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(module, "JiraClient", fake_client)
    return seen


def test_stdio_prefers_jira_credentials(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    _set_base_env(monkeypatch, "stdio")
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "conf@example.com")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "conf-token")
    monkeypatch.setenv("JIRA_USER_EMAIL", "jira@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "jira-token")
    module.get_jira_client()
    assert (captured["email"], captured["token"]) == ("jira@example.com", "jira-token")


def test_stdio_falls_back_to_confluence_credentials(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    _set_base_env(monkeypatch, "stdio")
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "conf@example.com")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "conf-token")
    module.get_jira_client()
    assert (captured["email"], captured["token"]) == ("conf@example.com", "conf-token")
    assert captured["base_url"] == "https://test.atlassian.net"


def test_half_jira_pair_is_error_not_mixed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch, "stdio")
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "conf@example.com")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "conf-token")
    monkeypatch.setenv("JIRA_USER_EMAIL", "jira@example.com")
    with pytest.raises(MissingJiraCredentialsError, match="together"):
        module.get_jira_client()


def test_http_uses_jira_headers(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    _set_base_env(monkeypatch, "http")
    monkeypatch.setattr(
        module,
        "get_http_headers",
        lambda: {"x-jira-user-email": "alice@example.com", "x-jira-api-token": "alice-jira"},
    )
    module.get_jira_client()
    assert (captured["email"], captured["token"]) == ("alice@example.com", "alice-jira")


def test_http_falls_back_to_confluence_headers(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    _set_base_env(monkeypatch, "http")
    monkeypatch.setattr(
        module,
        "get_http_headers",
        lambda: {
            "x-confluence-user-email": "alice@example.com",
            "x-confluence-api-token": "alice-conf",
        },
    )
    module.get_jira_client()
    assert (captured["email"], captured["token"]) == ("alice@example.com", "alice-conf")


def test_http_never_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch, "http")
    monkeypatch.setenv("JIRA_USER_EMAIL", "service@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "service-token")
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "service@example.com")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "service-token")
    monkeypatch.setattr(module, "get_http_headers", lambda: {})
    with pytest.raises(MissingJiraCredentialsError):
        module.get_jira_client()


# --- execute_bulk_create ---------------------------------------------------------


def _items(*summaries: str) -> list[module.PreparedItem]:
    return [
        module.PreparedItem(index=i, summary=s, fields={"summary": s})
        for i, s in enumerate(summaries)
    ]


@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.browse_url = lambda key: f"https://test.atlassian.net/browse/{key}"
    return client


def _bulk(created: dict[int, str], errors: dict[int, str] | None = None) -> AsyncMock:
    return AsyncMock(
        return_value=BulkCreateResponse(
            created={i: CreatedIssue(id=str(i), key=k) for i, k in created.items()},
            errors=errors or {},
        )
    )


async def test_all_created(monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock) -> None:
    monkeypatch.setattr(module, "bulk_create_issues", _bulk({0: "P-1", 1: "P-2"}))
    link = AsyncMock()
    monkeypatch.setattr(module, "create_clone_link", link)
    result = await module.execute_bulk_create(
        fake_client, {}, _items("a", "b"), {}, link_template_key="P-100"
    )
    assert result["status"] == "CREATED"
    assert result["created"] == 2
    assert [r["issue_key"] for r in result["results"]] == ["P-1", "P-2"]
    assert link.await_count == 2


async def test_partial(monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock) -> None:
    monkeypatch.setattr(module, "bulk_create_issues", _bulk({0: "P-1"}, {1: "bad option"}))
    result = await module.execute_bulk_create(fake_client, {}, _items("a", "b"), {})
    assert result["status"] == "PARTIAL"
    assert result["results"][1] == {
        "index": 1,
        "summary": "b",
        "status": "FAILED",
        "error": "bad option",
    }


async def test_none_created_is_error(
    monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock
) -> None:
    monkeypatch.setattr(module, "bulk_create_issues", _bulk({}, {0: "bad"}))
    result = await module.execute_bulk_create(fake_client, {}, _items("a"), {})
    assert result["status"] == "ERROR"
    assert result["results"][0]["status"] == "FAILED"


async def test_skips_existing_and_sends_only_new(
    monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock
) -> None:
    bulk = _bulk({0: "P-2"})
    monkeypatch.setattr(module, "bulk_create_issues", bulk)
    result = await module.execute_bulk_create(fake_client, {}, _items("a", "b"), {"a": "P-1"})
    assert bulk.call_args.args[1] == [{"summary": "b"}]
    assert result["status"] == "CREATED"
    assert result["skipped_existing"] == 1
    assert result["results"][0]["status"] == "SKIPPED_EXISTS"
    assert result["results"][1]["issue_key"] == "P-2"


async def test_all_existing_is_no_changes_without_write(
    monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock
) -> None:
    bulk = _bulk({})
    monkeypatch.setattr(module, "bulk_create_issues", bulk)
    result = await module.execute_bulk_create(fake_client, {}, _items("a"), {"a": "P-1"})
    assert result["status"] == "NO_CHANGES"
    bulk.assert_not_called()


async def test_link_failure_keeps_created(
    monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock
) -> None:
    monkeypatch.setattr(module, "bulk_create_issues", _bulk({0: "P-1"}))
    monkeypatch.setattr(
        module, "create_clone_link", AsyncMock(side_effect=JiraAPIError("link type missing"))
    )
    result = await module.execute_bulk_create(
        fake_client, {}, _items("a"), {}, link_template_key="P-100"
    )
    assert result["status"] == "CREATED"
    assert result["results"][0]["link_error"] == "link type missing"


def test_dry_run_lists_every_item() -> None:
    items = [
        module.PreparedItem(index=0, summary="a", fields={}, overrides=["labels"]),
        module.PreparedItem(index=1, summary="b", fields={}),
    ]
    result = module.build_bulk_dry_run({"project_key": "P"}, items, {"b": "P-9"})
    assert result["status"] == "DRY_RUN"
    assert result["to_create"] == 1
    assert result["items"] == [
        {"index": 0, "summary": "a", "status": "WILL_CREATE", "overrides": ["labels"]},
        {"index": 1, "summary": "b", "status": "SKIPPED_EXISTS", "existing_issue_key": "P-9"},
    ]
