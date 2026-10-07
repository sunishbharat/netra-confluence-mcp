from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import netra_jira.tools.clone_issue as clone_module
import netra_jira.tools.create_issue as create_module
import netra_jira.tools.edit_issue as edit_module
import netra_jira.tools.shared as shared
from exceptions import JiraIssueNotFoundError
from models.jira import BulkCreateResponse, CreatedIssue, FieldMeta, IssueTypeMeta, JiraIssue
from netra_jira.fields import plain_text_to_adf

_CREATE_FIELDS = [
    FieldMeta(field_id="summary", name="Summary", required=True),
    FieldMeta(field_id="project", name="Project", required=True),
    FieldMeta(field_id="issuetype", name="Issue Type", required=True),
    FieldMeta(field_id="description", name="Description"),
    FieldMeta(field_id="labels", name="Labels"),
    FieldMeta(field_id="components", name="Components"),
    FieldMeta(field_id="customfield_10010", name="Service Name", required=True),
]

_TEMPLATE = JiraIssue(
    id="10001",
    key="PROJ-124",
    project_key="PROJ",
    issue_type_name="Task",
    summary="TEMPLATE",
    fields={
        "summary": "TEMPLATE",
        "labels": ["template"],
        "components": [{"id": "500", "name": "Backend"}],
        "customfield_10010": {"id": "900", "value": "Payment"},
        "assignee": {"accountId": "someone"},
    },
)


def _fake_client() -> MagicMock:
    fake = MagicMock()
    fake.browse_url = lambda key: f"https://test.atlassian.net/browse/{key}"
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    return fake


@pytest.fixture
def bulk(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Bulk create that succeeds for every item; also the write-call spy."""

    async def create(client: Any, field_sets: list[dict[str, Any]]) -> BulkCreateResponse:  # noqa: ANN401
        return BulkCreateResponse(
            created={
                i: CreatedIssue(id=str(i), key=f"PROJ-{400 + i}") for i in range(len(field_sets))
            }
        )

    mock = AsyncMock(side_effect=create)
    monkeypatch.setattr(shared, "bulk_create_issues", mock)
    monkeypatch.setattr(shared, "create_clone_link", AsyncMock())
    return mock


def _patch_common(monkeypatch: pytest.MonkeyPatch, module: Any, existing: dict[str, str]) -> None:  # noqa: ANN401
    monkeypatch.setattr(module, "get_jira_client", _fake_client)
    monkeypatch.setattr(
        module,
        "get_project_issue_types",
        AsyncMock(return_value=[IssueTypeMeta(id="3", name="Task")]),
    )
    monkeypatch.setattr(module, "get_create_fields", AsyncMock(return_value=_CREATE_FIELDS))
    monkeypatch.setattr(module, "find_existing", AsyncMock(return_value=existing))


@pytest.fixture
def clone_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, clone_module, {})
    monkeypatch.setattr(clone_module, "get_issue", AsyncMock(return_value=_TEMPLATE))


# --- clone_jira_issue ------------------------------------------------------------


async def test_clone_dry_run_never_writes(clone_env: None, bulk: AsyncMock) -> None:
    result = await clone_module.clone_jira_issue(
        "PROJ-124",
        [{"summary": "ProjectX A Payment"}, {"summary": "ProjectX A Billing"}],
    )
    assert result["status"] == "DRY_RUN"
    assert result["to_create"] == 2
    assert result["copied_fields"] == ["components", "customfield_10010", "labels"]
    bulk.assert_not_called()


async def test_clone_apply_sends_copied_fields_and_overrides(
    clone_env: None, bulk: AsyncMock
) -> None:
    result = await clone_module.clone_jira_issue(
        "PROJ-124",
        [{"summary": "ProjectX A Payment", "field_overrides": {"Labels": ["svc"]}}],
        dry_run=False,
    )
    assert result["status"] == "CREATED"
    sent = bulk.call_args.args[1][0]
    assert sent == {
        "labels": ["svc"],
        "components": [{"id": "500"}],
        "customfield_10010": {"id": "900"},
        "project": {"key": "PROJ"},
        "issuetype": {"id": "3"},
        "summary": "ProjectX A Payment",
    }
    shared.create_clone_link.assert_awaited_once()  # type: ignore[attr-defined]


async def test_clone_without_link(clone_env: None, bulk: AsyncMock) -> None:
    await clone_module.clone_jira_issue(
        "PROJ-124", [{"summary": "s"}], link_to_template=False, dry_run=False
    )
    shared.create_clone_link.assert_not_awaited()  # type: ignore[attr-defined]


async def test_clone_cross_project_drops_components(clone_env: None, bulk: AsyncMock) -> None:
    result = await clone_module.clone_jira_issue(
        "PROJ-124", [{"summary": "s"}], target_project_key="OTHER"
    )
    assert result["dropped_fields"] == ["components"]
    assert result["target_project"] == "OTHER"


async def test_clone_unknown_override_blocks_write(clone_env: None, bulk: AsyncMock) -> None:
    result = await clone_module.clone_jira_issue(
        "PROJ-124", [{"summary": "s", "field_overrides": {"Nope": 1}}], dry_run=False
    )
    assert result["status"] == "VALIDATION_FAILED"
    bulk.assert_not_called()


async def test_clone_missing_required_blocks_write(
    monkeypatch: pytest.MonkeyPatch, clone_env: None, bulk: AsyncMock
) -> None:
    bare = _TEMPLATE.model_copy(update={"fields": {"summary": "TEMPLATE"}})
    monkeypatch.setattr(clone_module, "get_issue", AsyncMock(return_value=bare))
    result = await clone_module.clone_jira_issue("PROJ-124", [{"summary": "s"}], dry_run=False)
    assert result["status"] == "VALIDATION_FAILED"
    assert "Service Name" in result["errors"][0]
    bulk.assert_not_called()


async def test_clone_issue_type_missing_in_target(
    monkeypatch: pytest.MonkeyPatch, clone_env: None
) -> None:
    monkeypatch.setattr(
        clone_module,
        "get_project_issue_types",
        AsyncMock(return_value=[IssueTypeMeta(id="1", name="Bug")]),
    )
    result = await clone_module.clone_jira_issue("PROJ-124", [{"summary": "s"}])
    assert result["status"] == "VALIDATION_FAILED"


async def test_clone_invalid_items_is_error(clone_env: None) -> None:
    result = await clone_module.clone_jira_issue("PROJ-124", [])
    assert result["status"] == "ERROR"


async def test_clone_api_error_is_error(monkeypatch: pytest.MonkeyPatch, clone_env: None) -> None:
    monkeypatch.setattr(
        clone_module, "get_issue", AsyncMock(side_effect=JiraIssueNotFoundError("no PROJ-124"))
    )
    result = await clone_module.clone_jira_issue("PROJ-124", [{"summary": "s"}])
    assert result == {"status": "ERROR", "error": "no PROJ-124"}


async def test_clone_all_existing_is_no_changes(
    monkeypatch: pytest.MonkeyPatch, clone_env: None, bulk: AsyncMock
) -> None:
    monkeypatch.setattr(clone_module, "find_existing", AsyncMock(return_value={"s": "PROJ-7"}))
    result = await clone_module.clone_jira_issue("PROJ-124", [{"summary": "s"}], dry_run=False)
    assert result["status"] == "NO_CHANGES"
    bulk.assert_not_called()


# --- create_jira_issue -----------------------------------------------------------


@pytest.fixture
def create_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, create_module, {})


async def test_create_dry_run_never_writes(create_env: None, bulk: AsyncMock) -> None:
    result = await create_module.create_jira_issue(
        "PROJ", "task", [{"summary": "s", "fields": {"customfield_10010": {"id": "1"}}}]
    )
    assert result["status"] == "DRY_RUN"
    assert result["issue_type"] == "Task"
    bulk.assert_not_called()


async def test_create_apply_wraps_description(create_env: None, bulk: AsyncMock) -> None:
    result = await create_module.create_jira_issue(
        "PROJ",
        "Task",
        [{"summary": "s", "description": "hello", "fields": {"Service Name": {"id": "1"}}}],
        dry_run=False,
    )
    assert result["status"] == "CREATED"
    sent = bulk.call_args.args[1][0]
    assert sent["description"] == plain_text_to_adf("hello")
    assert sent["customfield_10010"] == {"id": "1"}
    shared.create_clone_link.assert_not_awaited()  # type: ignore[attr-defined]


async def test_create_unknown_issue_type(create_env: None) -> None:
    result = await create_module.create_jira_issue("PROJ", "Epic", [{"summary": "s"}])
    assert result["status"] == "VALIDATION_FAILED"
    assert "Epic" in result["errors"][0]


async def test_create_description_twice_is_validation_failed(create_env: None) -> None:
    result = await create_module.create_jira_issue(
        "PROJ",
        "Task",
        [
            {
                "summary": "s",
                "description": "a",
                "fields": {"description": "b", "customfield_10010": {"id": "1"}},
            }
        ],
    )
    assert result["status"] == "VALIDATION_FAILED"


# --- edit_jira_issue -------------------------------------------------------------

_ISSUE = JiraIssue(
    id="1",
    key="PROJ-412",
    project_key="PROJ",
    issue_type_name="Task",
    summary="Old",
    fields={"summary": "Old", "labels": ["a"], "description": plain_text_to_adf("old")},
)

_EDIT_FIELDS = [
    FieldMeta(field_id="summary", name="Summary"),
    FieldMeta(field_id="labels", name="Labels"),
    FieldMeta(field_id="description", name="Description"),
]


@pytest.fixture
def edit_env(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(edit_module, "get_jira_client", _fake_client)
    monkeypatch.setattr(edit_module, "get_issue", AsyncMock(return_value=_ISSUE))
    monkeypatch.setattr(edit_module, "get_edit_fields", AsyncMock(return_value=_EDIT_FIELDS))
    update = AsyncMock()
    monkeypatch.setattr(edit_module, "update_issue", update)
    return update


async def test_edit_dry_run_shows_before_after(edit_env: AsyncMock) -> None:
    result = await edit_module.edit_jira_issue("PROJ-412", summary="New", description="new")
    assert result["status"] == "DRY_RUN"
    assert result["changes"] == [
        {"field": "summary", "before": "Old", "after": "New"},
        {"field": "description", "before": "old", "after": "new"},
    ]
    edit_env.assert_not_called()


async def test_edit_apply_writes_only_changed_fields(edit_env: AsyncMock) -> None:
    result = await edit_module.edit_jira_issue(
        "PROJ-412",
        summary="Old",
        field_overrides={"labels": ["a", "b"]},
        dry_run=False,
    )
    assert result["status"] == "UPDATED"
    assert result["fields_updated"] == ["labels"]
    edit_env.assert_awaited_once()
    assert edit_env.call_args.args[2] == {"labels": ["a", "b"]}


async def test_edit_same_values_is_no_changes(edit_env: AsyncMock) -> None:
    result = await edit_module.edit_jira_issue("PROJ-412", summary="Old", dry_run=False)
    assert result["status"] == "NO_CHANGES"
    edit_env.assert_not_called()


async def test_edit_invalid_summary_is_validation_failed(edit_env: AsyncMock) -> None:
    result = await edit_module.edit_jira_issue("PROJ-412", summary="a\nb")
    assert result["status"] == "VALIDATION_FAILED"


async def test_edit_unknown_field_is_validation_failed(edit_env: AsyncMock) -> None:
    result = await edit_module.edit_jira_issue("PROJ-412", field_overrides={"Nope": 1})
    assert result["status"] == "VALIDATION_FAILED"
    edit_env.assert_not_called()
