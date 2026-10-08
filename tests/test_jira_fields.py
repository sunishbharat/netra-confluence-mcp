from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from models.jira import MAX_BULK_ITEMS, CloneBatch, CreateBatch, FieldMeta, IssueSearchQuery
from netra_jira.fields import (
    adf_to_text,
    build_clone_fields,
    compute_field_changes,
    escape_jql_string,
    missing_required_fields,
    normalize_field_value,
    plain_text_to_adf,
    resolve_field_ids,
    resolve_field_keys,
    summary_search_jql,
    text_search_phrase,
)

_ADF = {
    "type": "doc",
    "version": 1,
    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Body"}]}],
}

_CREATE_FIELDS = [
    FieldMeta(field_id="summary", name="Summary", required=True),
    FieldMeta(field_id="project", name="Project", required=True),
    FieldMeta(field_id="issuetype", name="Issue Type", required=True),
    FieldMeta(field_id="reporter", name="Reporter", required=True, has_default_value=True),
    FieldMeta(field_id="description", name="Description"),
    FieldMeta(field_id="labels", name="Labels"),
    FieldMeta(field_id="priority", name="Priority"),
    FieldMeta(field_id="components", name="Components"),
    FieldMeta(field_id="customfield_10010", name="Service Name", required=True),
]

_TEMPLATE_FIELDS: dict[str, Any] = {
    "summary": "TEMPLATE - service",
    "project": {"id": "100", "key": "PROJ"},
    "issuetype": {"id": "3", "name": "Task"},
    "reporter": {"accountId": "abc", "displayName": "Template Owner"},
    "assignee": {"accountId": "def", "displayName": "Someone"},
    "description": _ADF,
    "labels": ["release", "template"],
    "priority": {"id": "2", "name": "High", "iconUrl": "https://x/high.svg"},
    "components": [{"id": "500", "name": "Backend", "self": "https://x/c/500"}],
    "customfield_10010": {"id": "900", "value": "Payment", "self": "https://x/o/900"},
    "customfield_99999": "not on create screen",
    "status": {"id": "1", "name": "Open"},
    "environment": None,
}


# --- normalize_field_value -------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", [], {}])
def test_normalize_empty_values_are_skipped(value: object) -> None:
    assert normalize_field_value(value) is None


def test_normalize_adf_passes_through() -> None:
    assert normalize_field_value(_ADF) == _ADF


def test_normalize_user_keeps_only_account_id() -> None:
    assert normalize_field_value({"accountId": "abc", "displayName": "X"}) == {"accountId": "abc"}


def test_normalize_object_with_id_keeps_only_id() -> None:
    assert normalize_field_value({"id": "2", "name": "High", "iconUrl": "u"}) == {"id": "2"}


def test_normalize_cascading_select_keeps_child() -> None:
    value = {"id": "1", "value": "Parent", "child": {"id": "2", "value": "Child"}}
    assert normalize_field_value(value) == {"id": "1", "child": {"id": "2"}}


def test_normalize_list_maps_each_element() -> None:
    assert normalize_field_value([{"id": "1", "name": "a"}, {"id": "2"}]) == [
        {"id": "1"},
        {"id": "2"},
    ]


def test_normalize_scalars_pass_through() -> None:
    assert normalize_field_value("text") == "text"
    assert normalize_field_value(3.5) == 3.5
    assert normalize_field_value(["a", "b"]) == ["a", "b"]


# --- build_clone_fields ----------------------------------------------------------


def test_clone_copies_create_screen_fields_only() -> None:
    copied, dropped = build_clone_fields(_TEMPLATE_FIELDS, _CREATE_FIELDS, cross_project=False)
    assert copied == {
        "description": _ADF,
        "labels": ["release", "template"],
        "priority": {"id": "2"},
        "components": [{"id": "500"}],
        "customfield_10010": {"id": "900"},
    }
    assert dropped == []


def test_clone_never_copies_excluded_fields() -> None:
    copied, _ = build_clone_fields(_TEMPLATE_FIELDS, _CREATE_FIELDS, cross_project=False)
    for excluded in ("summary", "project", "issuetype", "reporter", "assignee"):
        assert excluded not in copied


def test_clone_cross_project_drops_project_scoped_fields() -> None:
    copied, dropped = build_clone_fields(_TEMPLATE_FIELDS, _CREATE_FIELDS, cross_project=True)
    assert "components" not in copied
    assert dropped == ["components"]


# --- resolve_field_keys ----------------------------------------------------------


def test_resolve_by_field_id() -> None:
    resolved, errors = resolve_field_keys({"labels": ["x"]}, _CREATE_FIELDS)
    assert resolved == {"labels": ["x"]}
    assert errors == []


def test_resolve_by_case_insensitive_name() -> None:
    resolved, errors = resolve_field_keys({"service name": {"id": "901"}}, _CREATE_FIELDS)
    assert resolved == {"customfield_10010": {"id": "901"}}
    assert errors == []


def test_resolve_unknown_key_is_error() -> None:
    _, errors = resolve_field_keys({"Nope": 1}, _CREATE_FIELDS)
    assert len(errors) == 1
    assert "unknown field 'Nope'" in errors[0]


def test_resolve_ambiguous_name_is_error() -> None:
    meta = [
        FieldMeta(field_id="customfield_1", name="Team"),
        FieldMeta(field_id="customfield_2", name="team"),
    ]
    _, errors = resolve_field_keys({"Team": "x"}, meta)
    assert "ambiguous" in errors[0]


@pytest.mark.parametrize("key", ["summary", "project", "issuetype", "Summary"])
def test_resolve_rejects_tool_controlled_fields(key: str) -> None:
    _, errors = resolve_field_keys({key: "x"}, _CREATE_FIELDS)
    assert len(errors) == 1
    assert "cannot be overridden" in errors[0]


def test_resolve_same_field_twice_is_error() -> None:
    _, errors = resolve_field_keys(
        {"customfield_10010": {"id": "1"}, "Service Name": {"id": "2"}}, _CREATE_FIELDS
    )
    assert "more than once" in errors[0]


# --- missing_required_fields -----------------------------------------------------


def test_missing_required_reports_names_without_defaults() -> None:
    fields = {"summary": "s", "project": {"key": "P"}, "issuetype": {"id": "3"}}
    assert missing_required_fields(fields, _CREATE_FIELDS) == ["Service Name"]


def test_missing_required_empty_when_all_present() -> None:
    fields = {
        "summary": "s",
        "project": {"key": "P"},
        "issuetype": {"id": "3"},
        "customfield_10010": {"id": "1"},
    }
    assert missing_required_fields(fields, _CREATE_FIELDS) == []


# --- ADF helpers -----------------------------------------------------------------


def test_plain_text_to_adf_paragraphs_and_breaks() -> None:
    adf = plain_text_to_adf("line one\nline two\n\nsecond paragraph")
    assert adf["type"] == "doc"
    first, second = adf["content"]
    assert first["content"] == [
        {"type": "text", "text": "line one"},
        {"type": "hardBreak"},
        {"type": "text", "text": "line two"},
    ]
    assert second["content"] == [{"type": "text", "text": "second paragraph"}]


def test_plain_text_to_adf_empty_text_has_no_paragraphs() -> None:
    assert plain_text_to_adf("")["content"] == []


def test_adf_to_text_round_trip() -> None:
    assert adf_to_text(plain_text_to_adf("a\n\nb")) == "a\nb"


# --- JQL helpers -----------------------------------------------------------------


def test_escape_jql_string() -> None:
    assert escape_jql_string('a "b" \\c') == 'a \\"b\\" \\\\c'


def test_text_search_phrase_strips_lucene_specials() -> None:
    assert text_search_phrase("ProjectX - Variant[A]: Payment+") == "ProjectX Variant A Payment"


def test_summary_search_jql_shape() -> None:
    jql = summary_search_jql("PROJ", "ProjectX Variant-A Payment")
    assert jql == 'project = "PROJ" AND summary ~ "\\"ProjectX Variant A Payment\\""'


def test_summary_search_jql_none_when_nothing_searchable() -> None:
    assert summary_search_jql("PROJ", "-- ++") is None


# --- compute_field_changes -------------------------------------------------------


def test_changes_ignore_equivalent_values() -> None:
    current = {"priority": {"id": "2", "name": "High", "iconUrl": "u"}, "labels": ["a"]}
    assert compute_field_changes(current, {"priority": {"id": "2"}, "labels": ["a"]}) == []


def test_changes_report_before_and_after() -> None:
    current = {"summary": "Old", "description": plain_text_to_adf("old text")}
    changes = compute_field_changes(
        current, {"summary": "New", "description": plain_text_to_adf("new text")}
    )
    assert changes == [
        {"field": "summary", "before": "Old", "after": "New"},
        {"field": "description", "before": "old text", "after": "new text"},
    ]


# --- input models ----------------------------------------------------------------


def test_batch_rejects_empty_list() -> None:
    with pytest.raises(ValidationError):
        CloneBatch(items=[])


def test_batch_rejects_more_than_max_items() -> None:
    items = [{"summary": f"s{i}"} for i in range(MAX_BULK_ITEMS + 1)]
    with pytest.raises(ValidationError):
        CreateBatch(items=items)  # type: ignore[arg-type]


def test_batch_rejects_duplicate_summaries() -> None:
    with pytest.raises(ValidationError, match="unique"):
        CloneBatch(items=[{"summary": "a"}, {"summary": " a "}])  # type: ignore[list-item]


@pytest.mark.parametrize("summary", ["   ", "x" * 256, "line\nbreak"])
def test_item_rejects_invalid_summary(summary: str) -> None:
    with pytest.raises(ValidationError):
        CloneBatch(items=[{"summary": summary}])  # type: ignore[list-item]


def test_item_strips_summary() -> None:
    batch = CloneBatch(items=[{"summary": "  ProjectX A Payment  "}])  # type: ignore[list-item]
    assert batch.items[0].summary == "ProjectX A Payment"


def test_item_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        CloneBatch(items=[{"summary": "a", "assignee": "x"}])  # type: ignore[list-item]


# --- read-side helpers -----------------------------------------------------------

_SITE_FIELDS = [
    FieldMeta(field_id="labels", name="Labels"),
    FieldMeta(field_id="customfield_10010", name="Service Name"),
    FieldMeta(field_id="customfield_1", name="Team"),
    FieldMeta(field_id="customfield_2", name="Team"),
]


def test_resolve_field_ids_by_id_and_name() -> None:
    ids, errors = resolve_field_ids(["labels", "service name", "customfield_10010"], _SITE_FIELDS)
    assert ids == ["labels", "customfield_10010"]
    assert errors == []


def test_resolve_field_ids_unknown_and_ambiguous() -> None:
    ids, errors = resolve_field_ids(["Nope", "Team", "customfield_2"], _SITE_FIELDS)
    assert ids == ["customfield_2"]
    assert errors == [
        "unknown field 'Nope'",
        "field name 'Team' is ambiguous (customfield_1, customfield_2); use the field ID",
    ]


def test_search_query_strips_jql() -> None:
    assert IssueSearchQuery(jql="  project = PROJ  ").jql == "project = PROJ"


@pytest.mark.parametrize(
    "kwargs",
    [{"jql": "  "}, {"jql": "x", "max_results": 0}, {"jql": "x", "max_results": 101}],
)
def test_search_query_rejects_invalid(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        IssueSearchQuery(**kwargs)
