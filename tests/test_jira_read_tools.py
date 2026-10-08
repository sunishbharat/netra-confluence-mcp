from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import netra_jira.tools.read_issue as read_module
import netra_jira.tools.search_issues as search_module
from exceptions import JiraAPIError, JiraIssueNotFoundError
from models.jira import (
    CommentPage,
    FieldMeta,
    JiraComment,
    JiraIssue,
    SearchHit,
    SearchResult,
)
from netra_jira.display import describe_issue, issue_links, readable_value
from netra_jira.fields import plain_text_to_adf

_ISSUE = JiraIssue(
    id="1",
    key="PROJ-1285",
    project_key="PROJ",
    issue_type_name="Story",
    summary="Template",
    fields={
        "summary": "Template",
        "issuetype": {"id": "10", "name": "Story"},
        "status": {"id": "1", "name": "In Progress"},
        "priority": {"id": "3", "name": "High"},
        "assignee": None,
        "reporter": {"accountId": "u1", "displayName": "Alice"},
        "labels": ["hadoop"],
        "components": [{"id": "500", "name": "Backend"}],
        "description": plain_text_to_adf("Do the thing"),
        "parent": {"key": "PROJ-1", "fields": {"summary": "Epic", "status": {"name": "Open"}}},
        "subtasks": [{"key": "PROJ-2", "fields": {"summary": "Sub", "status": {"name": "Done"}}}],
        "issuelinks": [
            {
                "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "inwardIssue": {"key": "PROJ-3", "fields": {"summary": "Infra"}},
            }
        ],
        "attachment": [{"filename": "spec.pdf"}],
        "comment": {"comments": [], "total": 0},
        "watches": {"watchCount": 2},
        "customfield_10010": {"id": "900", "value": "Payment"},
        "customfield_10020": plain_text_to_adf("Given X then Y"),
        "customfield_10030": None,
    },
    field_names={"customfield_10010": "Service Name", "customfield_10020": "Acceptance Criteria"},
)


def _fake_client() -> MagicMock:
    fake = MagicMock()
    fake.browse_url = lambda key: f"https://test.atlassian.net/browse/{key}"
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    return fake


# --- display ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ([], None),
        ({"accountId": "u1", "displayName": "Alice"}, "Alice"),
        ({"accountId": "u1"}, "u1"),
        ({"id": "1", "value": "Region", "child": {"id": "2", "value": "EU"}}, "Region / EU"),
        ([{"id": "1", "name": "v1"}, {"id": "2", "name": "v2"}], ["v1", "v2"]),
        (plain_text_to_adf("hi"), "hi"),
        ({"originalEstimate": "1d"}, {"originalEstimate": "1d"}),
        (3.0, 3.0),
    ],
)
def test_readable_value(raw: object, expected: object) -> None:
    assert readable_value(raw) == expected


def test_describe_issue() -> None:
    result = describe_issue(_ISSUE, "https://test.atlassian.net/browse/PROJ-1285")
    assert result["issue_type"] == "Story"
    assert result["issue_status"] == "In Progress"
    assert result["assignee"] is None
    assert result["reporter"] == "Alice"
    assert result["components"] == ["Backend"]
    assert result["description"] == "Do the thing"
    assert result["parent"] == {"key": "PROJ-1", "summary": "Epic", "issue_status": "Open"}
    assert result["subtasks"] == [{"key": "PROJ-2", "summary": "Sub", "issue_status": "Done"}]
    assert result["links"] == [
        {"relation": "is blocked by", "key": "PROJ-3", "summary": "Infra", "issue_status": None}
    ]
    assert result["attachments"] == ["spec.pdf"]
    assert result["custom_fields"] == {
        "Acceptance Criteria": "Given X then Y",
        "Service Name": "Payment",
    }
    assert "comment" not in result
    assert "watches" not in result


def test_issue_links_outward() -> None:
    links = issue_links(
        [
            {
                "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "outwardIssue": {"key": "PROJ-9", "fields": {"summary": "Next"}},
            }
        ]
    )
    assert links[0]["relation"] == "blocks"
    assert links[0]["key"] == "PROJ-9"


def test_custom_fields_with_same_name_both_kept() -> None:
    issue = _ISSUE.model_copy(
        update={
            "fields": {"customfield_1": "a", "customfield_2": "b"},
            "field_names": {"customfield_1": "Team", "customfield_2": "Team"},
        }
    )
    result = describe_issue(issue, "url")
    assert result["custom_fields"] == {"Team": "a", "Team (customfield_2)": "b"}


# --- get_jira_issue --------------------------------------------------------------


@pytest.fixture
def read_env(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(read_module, "get_jira_client", _fake_client)
    monkeypatch.setattr(read_module, "get_issue", AsyncMock(return_value=_ISSUE))
    comments = AsyncMock(
        return_value=CommentPage(
            comments=[
                JiraComment(id="7", author="Bob", created="c", updated="u", body="Is it ready?")
            ],
            total=1,
        )
    )
    monkeypatch.setattr(read_module, "get_issue_comments", comments)
    return comments


async def test_get_issue_without_comments(read_env: AsyncMock) -> None:
    result = await read_module.get_jira_issue("PROJ-1285")
    assert result["status"] == "INSPECTION"
    assert result["key"] == "PROJ-1285"
    assert result["url"] == "https://test.atlassian.net/browse/PROJ-1285"
    assert "comments" not in result
    read_env.assert_not_called()
    read_module.get_issue.assert_awaited_once()  # type: ignore[attr-defined]
    assert read_module.get_issue.call_args.kwargs == {"with_names": True}  # type: ignore[attr-defined]


async def test_get_issue_with_comments(read_env: AsyncMock) -> None:
    result = await read_module.get_jira_issue("PROJ-1285", include_comments=True)
    assert result["comments"] == [
        {"id": "7", "author": "Bob", "created": "c", "updated": "u", "body": "Is it ready?"}
    ]
    assert result["comments_total"] == 1


async def test_get_issue_not_found_is_error(
    monkeypatch: pytest.MonkeyPatch, read_env: AsyncMock
) -> None:
    monkeypatch.setattr(
        read_module, "get_issue", AsyncMock(side_effect=JiraIssueNotFoundError("no PROJ-9"))
    )
    result = await read_module.get_jira_issue("PROJ-9")
    assert result == {"status": "ERROR", "error": "no PROJ-9"}


# --- search_jira_issues ----------------------------------------------------------

_HITS = SearchResult(
    issues=[
        SearchHit(
            key="PROJ-412",
            fields={
                "summary": "[Hadoop][vari-12] abc1",
                "status": {"name": "To Do"},
                "issuetype": {"name": "Story"},
                "assignee": None,
                "priority": {"name": "High"},
                "updated": "2026-10-08T10:00:00.000+0000",
                "customfield_10010": {"id": "900", "value": "Payment"},
            },
        )
    ],
    truncated=False,
)


@pytest.fixture
def search_env(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(search_module, "get_jira_client", _fake_client)
    monkeypatch.setattr(
        search_module,
        "get_all_fields",
        AsyncMock(return_value=[FieldMeta(field_id="customfield_10010", name="Service Name")]),
    )
    search = AsyncMock(return_value=_HITS)
    monkeypatch.setattr(search_module, "search_issues", search)
    return search


async def test_search_default_columns(search_env: AsyncMock) -> None:
    result = await search_module.search_jira_issues("project = PROJ")
    assert result["status"] == "INSPECTION"
    assert result["count"] == 1
    assert result["truncated"] is False
    assert result["issues"][0] == {
        "key": "PROJ-412",
        "url": "https://test.atlassian.net/browse/PROJ-412",
        "summary": "[Hadoop][vari-12] abc1",
        "issue_type": "Story",
        "issue_status": "To Do",
        "assignee": None,
        "priority": "High",
        "updated": "2026-10-08T10:00:00.000+0000",
    }
    search_module.get_all_fields.assert_not_called()  # type: ignore[attr-defined]
    jql, field_ids, max_results = search_env.call_args.args[1:]
    assert jql == "project = PROJ"
    assert field_ids == ["summary", "issuetype", "status", "assignee", "priority", "updated"]
    assert max_results == 50


async def test_search_extra_field_by_name(search_env: AsyncMock) -> None:
    result = await search_module.search_jira_issues("project = PROJ", fields=["Service Name"])
    assert result["issues"][0]["Service Name"] == "Payment"
    assert search_env.call_args.args[2][-1] == "customfield_10010"


async def test_search_unknown_field_is_validation_failed(search_env: AsyncMock) -> None:
    result = await search_module.search_jira_issues("project = PROJ", fields=["Nope"])
    assert result["status"] == "VALIDATION_FAILED"
    search_env.assert_not_called()


async def test_search_truncated_has_message(search_env: AsyncMock) -> None:
    search_env.return_value = _HITS.model_copy(update={"truncated": True})
    result = await search_module.search_jira_issues("project = PROJ", max_results=1)
    assert result["truncated"] is True
    assert "raise max_results" in result["message"]


@pytest.mark.parametrize("kwargs", [{"jql": " "}, {"jql": "x", "max_results": 500}])
async def test_search_invalid_input_is_error(
    search_env: AsyncMock, kwargs: dict[str, object]
) -> None:
    result = await search_module.search_jira_issues(**kwargs)  # type: ignore[arg-type]
    assert result["status"] == "ERROR"
    search_env.assert_not_called()


async def test_search_jira_error_is_error(search_env: AsyncMock) -> None:
    search_env.side_effect = JiraAPIError("Jira API error HTTP 400: bad JQL")
    result = await search_module.search_jira_issues("nope = 1")
    assert result == {"status": "ERROR", "error": "Jira API error HTTP 400: bad JQL"}
