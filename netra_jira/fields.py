"""Pure Jira field logic: copy, normalize, resolve, validate. No I/O."""

from __future__ import annotations

from typing import Any

from confluence.adf.walker import AdfWalker
from models.jira import FieldMeta

# Fields a template clone never copies:
#   summary            - always taken from the item
#   project, issuetype - set explicitly from the target
#   reporter           - Jira sets it to the calling user, preserving attribution
#   assignee           - the template's owner is not the clone's owner
#   attachment, issuelinks, parent - out of scope; the clone link is created separately
CLONE_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "summary",
        "project",
        "issuetype",
        "reporter",
        "assignee",
        "attachment",
        "issuelinks",
        "parent",
    }
)

# Fields whose values are IDs owned by one project; copying them into another project
# would reference components/versions that do not exist there.
PROJECT_SCOPED_FIELDS: frozenset[str] = frozenset({"components", "fixVersions", "versions"})

# Fields the tools set from their own arguments; an override for one of these would
# silently fight the tool's own value, so it is rejected instead.
TOOL_CONTROLLED_FIELDS: frozenset[str] = frozenset({"summary", "project", "issuetype"})

# Lucene query-syntax characters. Jira's text search tokenizer drops them anyway, and
# leaving them in a phrase risks a JQL parse error, so they become spaces in the search
# phrase. Correctness does not depend on the search: results are re-checked by exact
# string compare.
_TEXT_SEARCH_SPECIAL = set('+-&|!(){}[]^~*?\\:"/')


def is_empty(value: object) -> bool:
    """True for values Jira treats as 'not set': None, '', [], {}."""
    return value is None or value == "" or value == [] or value == {}


def is_adf_doc(value: object) -> bool:
    return isinstance(value, dict) and value.get("type") == "doc"


def normalize_field_value(value: object) -> Any:  # noqa: ANN401
    """Convert a value read from GET /issue into the shape the create API accepts.

    Returns None when the value is empty and the field should be skipped.
    """
    if is_empty(value):
        return None
    if is_adf_doc(value):
        return value
    if isinstance(value, list):
        items = [normalize_field_value(v) for v in value]
        kept = [v for v in items if v is not None]
        return kept or None
    if isinstance(value, dict):
        if "accountId" in value:
            return {"accountId": value["accountId"]}
        if "id" in value:
            normalized: dict[str, Any] = {"id": value["id"]}
            child = normalize_field_value(value.get("child"))
            if child is not None:
                normalized["child"] = child
            return normalized
        # Unknown object shape (e.g. timetracking): pass through and let Jira decide;
        # a rejection surfaces as that item's error, never a silent drop.
        return value
    return value


def build_clone_fields(
    template_fields: dict[str, Any],
    create_fields: list[FieldMeta],
    cross_project: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Select and normalize the template fields the target create screen accepts.

    Returns (copied_fields, dropped_fields). dropped_fields lists project-scoped fields
    the template had set but that cannot be copied into a different project.
    """
    allowed = {f.field_id for f in create_fields}
    copied: dict[str, Any] = {}
    dropped: list[str] = []
    for field_id, raw in template_fields.items():
        if field_id in CLONE_EXCLUDED_FIELDS or field_id not in allowed:
            continue
        value = normalize_field_value(raw)
        if value is None:
            continue
        if cross_project and field_id in PROJECT_SCOPED_FIELDS:
            dropped.append(field_id)
            continue
        copied[field_id] = value
    return copied, sorted(dropped)


def resolve_field_keys(
    overrides: dict[str, Any],
    field_meta: list[FieldMeta],
) -> tuple[dict[str, Any], list[str]]:
    """Map override keys (field ID or exact field name) to field IDs.

    Field ID match wins; otherwise a case-insensitive exact name match. Returns
    (resolved, errors); any error means the caller must not write.
    """
    by_id = {f.field_id: f for f in field_meta}
    by_name: dict[str, list[FieldMeta]] = {}
    for f in field_meta:
        by_name.setdefault(f.name.casefold(), []).append(f)

    resolved: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in overrides.items():
        if key in TOOL_CONTROLLED_FIELDS:
            errors.append(f"'{key}' is set by the tool arguments and cannot be overridden")
            continue
        if key in by_id:
            field_id = key
        else:
            matches = by_name.get(key.casefold(), [])
            if not matches:
                errors.append(f"unknown field '{key}' (not on this screen)")
                continue
            if len(matches) > 1:
                ids = ", ".join(m.field_id for m in matches)
                errors.append(f"field name '{key}' is ambiguous ({ids}); use the field ID")
                continue
            field_id = matches[0].field_id
        if field_id in TOOL_CONTROLLED_FIELDS:
            errors.append(f"'{key}' is set by the tool arguments and cannot be overridden")
            continue
        if field_id in resolved:
            errors.append(f"field '{field_id}' is given more than once")
            continue
        resolved[field_id] = value
    return resolved, errors


def resolve_field_ids(
    requested: list[str],
    field_meta: list[FieldMeta],
) -> tuple[list[str], list[str]]:
    """Map requested fields (field ID or exact field name) to field IDs, for reads.

    Same matching as resolve_field_keys: field ID first, then a case-insensitive exact
    name. Returns (field_ids, errors) with duplicates removed and request order kept.
    """
    by_id = {f.field_id for f in field_meta}
    by_name: dict[str, list[str]] = {}
    for f in field_meta:
        by_name.setdefault(f.name.casefold(), []).append(f.field_id)

    field_ids: list[str] = []
    errors: list[str] = []
    for key in requested:
        if key in by_id:
            field_id = key
        else:
            matches = by_name.get(key.casefold(), [])
            if not matches:
                errors.append(f"unknown field '{key}'")
                continue
            if len(matches) > 1:
                errors.append(
                    f"field name '{key}' is ambiguous ({', '.join(matches)}); use the field ID"
                )
                continue
            field_id = matches[0]
        if field_id not in field_ids:
            field_ids.append(field_id)
    return field_ids, errors


def missing_required_fields(fields: dict[str, Any], create_fields: list[FieldMeta]) -> list[str]:
    """Names of required fields with no value and no Jira default."""
    return [
        f.name
        for f in create_fields
        if f.required and not f.has_default_value and is_empty(fields.get(f.field_id))
    ]


def plain_text_to_adf(text: str) -> dict[str, Any]:
    """Wrap plain text into a minimal ADF document.

    Blank lines separate paragraphs; single newlines become hard breaks.
    """
    paragraphs: list[dict[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = block.split("\n")
        content: list[dict[str, Any]] = []
        for i, line in enumerate(lines):
            if i > 0:
                content.append({"type": "hardBreak"})
            if line:
                content.append({"type": "text", "text": line})
        if any(node["type"] == "text" for node in content):
            paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs}


def adf_to_text(adf: dict[str, Any]) -> str:
    """Flatten an ADF document's text nodes for human-readable previews."""
    parts: list[str] = []

    def visitor(node: dict[str, Any], path: list[str]) -> None:  # noqa: ARG001
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            parts.append(node["text"])
        elif node.get("type") in ("hardBreak", "paragraph") and parts:
            parts.append("\n")

    AdfWalker.walk(adf, visitor)
    return "".join(parts).strip()


def escape_jql_string(value: str) -> str:
    """Escape a value for use inside a double-quoted JQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def text_search_phrase(summary: str) -> str:
    """Reduce a summary to a phrase safe for `summary ~ "\\"<phrase>\\""`.

    Lucene special characters become spaces and whitespace is collapsed. Returns ''
    when nothing searchable is left.
    """
    cleaned = "".join(" " if ch in _TEXT_SEARCH_SPECIAL else ch for ch in summary)
    return " ".join(cleaned.split())


def summary_search_jql(project_key: str, summary: str) -> str | None:
    """JQL finding candidate issues with this summary in a project, or None if unsearchable."""
    phrase = text_search_phrase(summary)
    if not phrase:
        return None
    quoted_phrase = escape_jql_string(f'"{phrase}"')
    return f'project = "{escape_jql_string(project_key)}" AND summary ~ "{quoted_phrase}"'


def preview_value(value: object) -> object:
    """Human-readable form of a field value for dry-run diffs (ADF shown as text)."""
    if is_adf_doc(value):
        assert isinstance(value, dict)
        return adf_to_text(value)
    return value


def compute_field_changes(
    current_fields: dict[str, Any],
    new_fields: dict[str, Any],
) -> list[dict[str, Any]]:
    """Before/after entries for fields whose new value differs from the current one.

    Current values are normalized to create-API shape first, so an unchanged
    priority ({"id": "3"} vs the full priority object) is not reported as a change.
    """
    changes: list[dict[str, Any]] = []
    for field_id, new_value in new_fields.items():
        current = normalize_field_value(current_fields.get(field_id))
        target = normalize_field_value(new_value)
        if current == target:
            continue
        changes.append(
            {
                "field": field_id,
                "before": preview_value(current),
                "after": preview_value(target),
            }
        )
    return changes
