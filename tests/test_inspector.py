from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from confluence.adf.inspector import AdfInspector

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_adf.json"


@pytest.fixture
def adf() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text()))


def test_extract_jql_queries_count(adf: dict[str, Any]) -> None:
    results = AdfInspector.extract_jql_queries(adf)
    assert len(results) == 2


def test_extract_jql_queries_macro_ids(adf: dict[str, Any]) -> None:
    results = AdfInspector.extract_jql_queries(adf)
    ids = {r.macro_id for r in results}
    assert "macro-id-001" in ids
    assert "macro-id-002" in ids


def test_extract_jql_queries_jql_content(adf: dict[str, Any]) -> None:
    results = AdfInspector.extract_jql_queries(adf)
    jqls = [r.jql for r in results]
    assert any("ver_R1.0" in j and "ver_R1.0_Baseline" not in j for j in jqls)
    assert any("ver_R1.0_Baseline" in j for j in jqls)


def test_extract_jql_queries_columns_populated(adf: dict[str, Any]) -> None:
    results = AdfInspector.extract_jql_queries(adf)
    entry = next(r for r in results if r.macro_id == "macro-id-001")
    assert "key" in entry.columns


def test_extract_jql_queries_server_populated(adf: dict[str, Any]) -> None:
    results = AdfInspector.extract_jql_queries(adf)
    assert all(r.server == "MyJira" for r in results)


def test_extract_jql_queries_max_issues(adf: dict[str, Any]) -> None:
    results = AdfInspector.extract_jql_queries(adf)
    entry = next(r for r in results if r.macro_id == "macro-id-001")
    assert entry.max_issues == "20"


def test_extract_jql_location_paths_are_strings(adf: dict[str, Any]) -> None:
    results = AdfInspector.extract_jql_queries(adf)
    for r in results:
        assert isinstance(r.location_path, str)
        assert len(r.location_path) > 0


def test_extract_jql_location_paths_are_distinct(adf: dict[str, Any]) -> None:
    results = AdfInspector.extract_jql_queries(adf)
    paths = [r.location_path for r in results]
    assert len(paths) == len(set(paths))


def test_build_inspection_structure(adf: dict[str, Any]) -> None:
    inspection = AdfInspector.build_inspection("page-123", "R1.0 Report", adf)
    assert inspection.page_id == "page-123"
    assert inspection.title == "R1.0 Report"
    assert inspection.jira_macro_count == 2
    assert len(inspection.jql_queries) == 2


def test_unique_strings_contains_version_tokens(adf: dict[str, Any]) -> None:
    inspection = AdfInspector.build_inspection("p", "t", adf)
    assert "ver_R1.0" in inspection.unique_strings
    assert "ver_R1.0_Baseline" in inspection.unique_strings


def test_unique_strings_excludes_jql_keywords(adf: dict[str, Any]) -> None:
    inspection = AdfInspector.build_inspection("p", "t", adf)
    keywords = {"AND", "OR", "NOT", "IN", "IS"}
    assert not keywords.intersection(set(inspection.unique_strings))


def test_unique_strings_are_sorted(adf: dict[str, Any]) -> None:
    inspection = AdfInspector.build_inspection("p", "t", adf)
    assert inspection.unique_strings == sorted(inspection.unique_strings)


def test_no_jira_macros_returns_empty() -> None:
    adf: dict[str, Any] = {"type": "doc", "version": 1, "content": []}
    results = AdfInspector.extract_jql_queries(adf)
    assert results == []


def test_extension_without_jql_skipped() -> None:
    adf: dict[str, Any] = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "extension",
                "attrs": {
                    "extensionType": "com.atlassian.confluence.macro.core",
                    "extensionKey": "jira",
                    "parameters": {
                        "macroParams": {},
                        "macroMetadata": {"macroId": {"value": "x"}},
                    },
                },
            }
        ],
    }
    results = AdfInspector.extract_jql_queries(adf)
    assert results == []
