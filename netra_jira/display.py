"""Read-side formatting: raw Jira v3 field values to compact, readable values. No I/O."""

from __future__ import annotations

from typing import Any

from models.jira import JiraIssue, SearchHit
from netra_jira.fields import adf_to_text, is_adf_doc, is_empty

# System fields shown at the top level of a get_jira_issue response, as
# (Jira field ID, response key). Other system fields (watches, votes, worklog, the
# embedded comment page, ...) are omitted: they add bulk without helping a reader judge
# the ticket. Status is renamed issue_status so it never collides with the tool status.
_ISSUE_FIELDS: tuple[tuple[str, str], ...] = (
    ("issuetype", "issue_type"),
    ("status", "issue_status"),
    ("priority", "priority"),
    ("assignee", "assignee"),
    ("reporter", "reporter"),
    ("labels", "labels"),
    ("components", "components"),
    ("fixVersions", "fix_versions"),
    ("versions", "affects_versions"),
    ("resolution", "resolution"),
    ("duedate", "due_date"),
    ("created", "created"),
    ("updated", "updated"),
    ("environment", "environment"),
    ("description", "description"),
)

# Fields every search_jira_issues row carries, as (Jira field ID, row key).
SEARCH_FIELDS: tuple[tuple[str, str], ...] = (
    ("summary", "summary"),
    ("issuetype", "issue_type"),
    ("status", "issue_status"),
    ("assignee", "assignee"),
    ("priority", "priority"),
    ("updated", "updated"),
)

_CUSTOM_FIELD_PREFIX = "customfield_"


def readable_value(value: object) -> Any:  # noqa: ANN401
    """Reduce a field value read from Jira to what a person would see in the UI.

    ADF becomes plain text, users their display name, options and named objects
    (status, priority, component, version) their value or name, and an issue
    reference a {key, summary, issue_status} dict. Returns None for empty values.
    """
    if is_empty(value):
        return None
    if is_adf_doc(value):
        assert isinstance(value, dict)
        return adf_to_text(value) or None
    if isinstance(value, list):
        items = [readable_value(v) for v in value]
        kept = [v for v in items if v is not None]
        return kept or None
    if isinstance(value, dict):
        if "accountId" in value:
            return value.get("displayName") or value["accountId"]
        if "key" in value and isinstance(value.get("fields"), dict):
            return issue_ref(value)
        for label_key in ("value", "name"):
            label = value.get(label_key)
            if not is_empty(label):
                child = readable_value(value.get("child"))
                return f"{label} / {child}" if child is not None else label
        # Unknown object shape (e.g. timetracking): returned as Jira sent it.
        return value
    return value


def issue_ref(raw: dict[str, Any]) -> dict[str, Any]:
    """Compact reference to a related issue (parent, sub-task, linked issue)."""
    fields = raw.get("fields") or {}
    return {
        "key": str(raw.get("key", "")),
        "summary": fields.get("summary"),
        "issue_status": readable_value(fields.get("status")),
    }


def issue_links(raw_links: object) -> list[dict[str, Any]]:
    """Linked issues with the relation as read from this issue (e.g. 'blocks')."""
    links: list[dict[str, Any]] = []
    if not isinstance(raw_links, list):
        return links
    for link in raw_links:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        if isinstance(link.get("outwardIssue"), dict):
            relation, other = link_type.get("outward"), link["outwardIssue"]
        elif isinstance(link.get("inwardIssue"), dict):
            relation, other = link_type.get("inward"), link["inwardIssue"]
        else:
            continue
        links.append({"relation": relation or link_type.get("name"), **issue_ref(other)})
    return links


def _custom_fields(fields: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    """Non-empty custom fields keyed by display name, sorted by name."""
    entries: list[tuple[str, str, Any]] = []
    for field_id, raw in fields.items():
        if not field_id.startswith(_CUSTOM_FIELD_PREFIX):
            continue
        value = readable_value(raw)
        if value is not None:
            entries.append((names.get(field_id, field_id), field_id, value))

    result: dict[str, Any] = {}
    for name, field_id, value in sorted(entries, key=lambda e: (e[0].casefold(), e[1])):
        # Two custom fields can share a display name; the ID keeps both visible.
        result[f"{name} ({field_id})" if name in result else name] = value
    return result


def describe_issue(issue: JiraIssue, url: str) -> dict[str, Any]:
    """Readable view of one issue for get_jira_issue (no comments, no tool status)."""
    fields = issue.fields
    result: dict[str, Any] = {
        "key": issue.key,
        "url": url,
        "project": issue.project_key,
        "summary": issue.summary,
    }
    for field_id, response_key in _ISSUE_FIELDS:
        result[response_key] = readable_value(fields.get(field_id))

    parent = fields.get("parent")
    result["parent"] = issue_ref(parent) if isinstance(parent, dict) else None
    result["subtasks"] = [issue_ref(s) for s in fields.get("subtasks") or [] if isinstance(s, dict)]
    result["links"] = issue_links(fields.get("issuelinks"))
    result["attachments"] = [
        str(a["filename"])
        for a in fields.get("attachment") or []
        if isinstance(a, dict) and a.get("filename")
    ]
    result["custom_fields"] = _custom_fields(fields, issue.field_names)
    return result


def search_row(
    hit: SearchHit, url: str, extra_field_ids: list[str], names: dict[str, str]
) -> dict[str, Any]:
    """One search_jira_issues row: the standard columns plus requested extra fields."""
    row: dict[str, Any] = {"key": hit.key, "url": url}
    for field_id, row_key in SEARCH_FIELDS:
        row[row_key] = readable_value(hit.fields.get(field_id))
    for field_id in extra_field_ids:
        row[names.get(field_id, field_id)] = readable_value(hit.fields.get(field_id))
    return row
