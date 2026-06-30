from __future__ import annotations

from confluence.adf.differ import AdfDiffer
from models.adf import ChangeLogEntry


def _entry(location: str, detail: str = "x -> y") -> ChangeLogEntry:
    return ChangeLogEntry(location=location, detail=detail)


def test_empty_log_returns_no_changes() -> None:
    result = AdfDiffer.summarize_changes([])
    assert result == "No changes"


def test_single_entry_summarized() -> None:
    result = AdfDiffer.summarize_changes([_entry("jql:content/0")])
    assert "jql" in result
    assert "1 change(s)" in result


def test_groups_entries_by_prefix() -> None:
    log = [
        _entry("jql:content/0"),
        _entry("jql:content/1"),
        _entry("text:content/2"),
    ]
    result = AdfDiffer.summarize_changes(log)
    assert "jql: 2 change(s)" in result
    assert "text: 1 change(s)" in result


def test_groups_sorted_alphabetically() -> None:
    log = [
        _entry("text:content/0"),
        _entry("jql:content/0"),
        _entry("macro_param:columns:content/1"),
        _entry("title"),
    ]
    result = AdfDiffer.summarize_changes(log)
    lines = result.splitlines()
    prefixes = [line.split(":")[0] for line in lines]
    assert prefixes == sorted(prefixes)


def test_macro_param_prefix_extracted_correctly() -> None:
    log = [_entry("macro_param:columns:content/0/content/1")]
    result = AdfDiffer.summarize_changes(log)
    assert "macro_param" in result


def test_title_prefix() -> None:
    log = [_entry("title"), _entry("title")]
    result = AdfDiffer.summarize_changes(log)
    assert "title: 2 change(s)" in result
