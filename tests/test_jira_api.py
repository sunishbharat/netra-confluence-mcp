from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from exceptions import JiraAPIError
from netra_jira.api import (
    bulk_create_issues,
    create_clone_link,
    find_existing_summaries,
    get_all_fields,
    get_create_fields,
    get_edit_fields,
    get_issue,
    get_issue_comments,
    get_project_issue_types,
    search_issues,
    update_issue,
)
from netra_jira.client import JiraClient

BASE = "https://test.atlassian.net"
API = f"{BASE}/rest/api/3"


@pytest.fixture
def jira() -> JiraClient:
    return JiraClient(base_url=BASE, site_url=BASE, email="a@example.com", token="t")


async def test_get_issue(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/issue/PROJ-124",
        json={
            "id": "10001",
            "key": "PROJ-124",
            "fields": {
                "summary": "Template",
                "project": {"key": "PROJ"},
                "issuetype": {"id": "3", "name": "Task"},
            },
        },
    )
    issue = await get_issue(jira, "PROJ-124")
    assert issue.key == "PROJ-124"
    assert issue.project_key == "PROJ"
    assert issue.issue_type_name == "Task"
    assert issue.summary == "Template"


async def test_issue_types_paginate(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    path = f"{API}/issue/createmeta/PROJ/issuetypes"
    httpx_mock.add_response(
        url=f"{path}?startAt=0&maxResults=50",
        json={"issueTypes": [{"id": "1", "name": "Bug"}, {"id": "3", "name": "Task"}], "total": 3},
    )
    httpx_mock.add_response(
        url=f"{path}?startAt=2&maxResults=50",
        json={"issueTypes": [{"id": "5", "name": "Story"}], "total": 3},
    )
    types = await get_project_issue_types(jira, "PROJ")
    assert [t.name for t in types] == ["Bug", "Task", "Story"]


async def test_create_fields(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/issue/createmeta/PROJ/issuetypes/3?startAt=0&maxResults=50",
        json={
            "fields": [
                {"fieldId": "summary", "name": "Summary", "required": True},
                {
                    "fieldId": "reporter",
                    "name": "Reporter",
                    "required": True,
                    "hasDefaultValue": True,
                },
            ],
            "total": 2,
        },
    )
    fields = await get_create_fields(jira, "PROJ", "3")
    assert [f.field_id for f in fields] == ["summary", "reporter"]
    assert fields[1].has_default_value is True


async def test_edit_fields(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/issue/PROJ-1/editmeta",
        json={"fields": {"labels": {"name": "Labels", "required": False}}},
    )
    fields = await get_edit_fields(jira, "PROJ-1")
    assert fields[0].field_id == "labels"
    assert fields[0].name == "Labels"


async def test_find_existing_requires_exact_summary(
    httpx_mock: HTTPXMock, jira: JiraClient
) -> None:
    httpx_mock.add_response(
        url=f"{API}/search/jql",
        method="POST",
        json={
            "issues": [
                {"key": "PROJ-9", "fields": {"summary": "ProjectX A Payment Service v2"}},
                {"key": "PROJ-7", "fields": {"summary": "ProjectX A Payment Service"}},
            ]
        },
    )
    existing = await find_existing_summaries(jira, "PROJ", ["ProjectX A Payment Service"])
    assert existing == {"ProjectX A Payment Service": "PROJ-7"}
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["jql"] == 'project = "PROJ" AND summary ~ "\\"ProjectX A Payment Service\\""'


async def test_find_existing_none(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/search/jql",
        method="POST",
        json={"issues": [{"key": "PROJ-9", "fields": {"summary": "Something else"}}]},
    )
    assert await find_existing_summaries(jira, "PROJ", ["New one"]) == {}


async def test_bulk_create_partial_maps_indexes(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/issue/bulk",
        method="POST",
        status_code=201,
        json={
            "issues": [{"id": "1", "key": "PROJ-1"}, {"id": "3", "key": "PROJ-3"}],
            "errors": [
                {
                    "failedElementNumber": 1,
                    "status": 400,
                    "elementErrors": {"errors": {"customfield_10010": "invalid option"}},
                }
            ],
        },
    )
    result = await bulk_create_issues(jira, [{"summary": "a"}, {"summary": "b"}, {"summary": "c"}])
    assert {i: c.key for i, c in result.created.items()} == {0: "PROJ-1", 2: "PROJ-3"}
    assert result.errors == {1: "customfield_10010: invalid option"}
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {
        "issueUpdates": [
            {"fields": {"summary": "a"}},
            {"fields": {"summary": "b"}},
            {"fields": {"summary": "c"}},
        ]
    }


async def test_bulk_create_all_failed_400_is_parsed(
    httpx_mock: HTTPXMock, jira: JiraClient
) -> None:
    httpx_mock.add_response(
        url=f"{API}/issue/bulk",
        method="POST",
        status_code=400,
        json={
            "issues": [],
            "errors": [{"failedElementNumber": 0, "elementErrors": {"errorMessages": ["bad"]}}],
        },
    )
    result = await bulk_create_issues(jira, [{"summary": "a"}])
    assert result.created == {}
    assert result.errors == {0: "bad"}


async def test_bulk_create_malformed_400_raises(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/issue/bulk",
        method="POST",
        status_code=400,
        json={"errorMessages": ["issueUpdates is required"]},
    )
    with pytest.raises(JiraAPIError, match="issueUpdates is required"):
        await bulk_create_issues(jira, [{"summary": "a"}])


async def test_create_clone_link_payload(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(url=f"{API}/issueLink", method="POST", status_code=201)
    await create_clone_link(jira, "PROJ-412", "PROJ-124")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {
        "type": {"name": "Cloners"},
        "inwardIssue": {"key": "PROJ-412"},
        "outwardIssue": {"key": "PROJ-124"},
    }


async def test_update_issue_puts_fields(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(url=f"{API}/issue/PROJ-1", method="PUT", status_code=204)
    await update_issue(jira, "PROJ-1", {"summary": "New"})
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {"fields": {"summary": "New"}}


# --- read tools ------------------------------------------------------------------


async def test_get_issue_with_names(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/issue/PROJ-124?expand=names",
        json={
            "id": "10001",
            "key": "PROJ-124",
            "fields": {"summary": "Template", "customfield_10010": "x"},
            "names": {"summary": "Summary", "customfield_10010": "Service Name"},
        },
    )
    issue = await get_issue(jira, "PROJ-124", with_names=True)
    assert issue.field_names["customfield_10010"] == "Service Name"


async def test_get_issue_comments_newest_first(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/issue/PROJ-1/comment?orderBy=-created&maxResults=20",
        json={
            "total": 31,
            "comments": [
                {
                    "id": "7",
                    "author": {"accountId": "u1", "displayName": "Alice"},
                    "created": "2026-10-01T10:00:00.000+0000",
                    "updated": "2026-10-01T10:00:00.000+0000",
                    "body": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Ready?"}]}
                        ],
                    },
                }
            ],
        },
    )
    page = await get_issue_comments(jira, "PROJ-1", 20)
    assert page.total == 31
    assert page.comments[0].author == "Alice"
    assert page.comments[0].body == "Ready?"


async def test_get_all_fields(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/field",
        json=[{"id": "labels", "name": "Labels"}, {"id": "customfield_1", "name": "Team"}],
    )
    fields = await get_all_fields(jira)
    assert [(f.field_id, f.name) for f in fields] == [
        ("labels", "Labels"),
        ("customfield_1", "Team"),
    ]


async def test_search_issues_paginates_and_truncates(
    httpx_mock: HTTPXMock, jira: JiraClient
) -> None:
    httpx_mock.add_response(
        url=f"{API}/search/jql",
        method="POST",
        json={
            "issues": [{"key": "PROJ-1", "fields": {}}, {"key": "PROJ-2", "fields": {}}],
            "nextPageToken": "t1",
        },
    )
    httpx_mock.add_response(
        url=f"{API}/search/jql",
        method="POST",
        json={"issues": [{"key": "PROJ-3", "fields": {}}], "nextPageToken": "t2"},
    )
    result = await search_issues(jira, "project = PROJ", ["summary"], 3)
    assert [h.key for h in result.issues] == ["PROJ-1", "PROJ-2", "PROJ-3"]
    assert result.truncated is True
    first, second = (json.loads(r.content) for r in httpx_mock.get_requests())
    assert first == {"jql": "project = PROJ", "fields": ["summary"], "maxResults": 3}
    assert second["nextPageToken"] == "t1"
    assert second["maxResults"] == 1


async def test_search_issues_last_page_not_truncated(
    httpx_mock: HTTPXMock, jira: JiraClient
) -> None:
    httpx_mock.add_response(
        url=f"{API}/search/jql",
        method="POST",
        json={"issues": [{"key": "PROJ-1", "fields": {"summary": "a"}}]},
    )
    result = await search_issues(jira, "project = PROJ", ["summary"], 50)
    assert [h.key for h in result.issues] == ["PROJ-1"]
    assert result.truncated is False


async def test_search_issues_invalid_jql_raises(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(
        url=f"{API}/search/jql",
        method="POST",
        status_code=400,
        json={"errorMessages": ["Field 'nope' does not exist."]},
    )
    with pytest.raises(JiraAPIError, match="does not exist"):
        await search_issues(jira, "nope = 1", ["summary"], 50)
