from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from confluence.adf.validator import AdfValidator

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_adf.json"


@pytest.fixture
def adf() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text()))


def test_validate_passes_valid_fixture(adf: dict[str, Any]) -> None:
    errors = AdfValidator.validate(adf)
    assert errors == []


def test_validate_fails_wrong_root_type() -> None:
    adf: dict[str, Any] = {"type": "paragraph", "content": []}
    errors = AdfValidator.validate(adf)
    assert any("doc" in e for e in errors)


def test_validate_fails_content_not_list() -> None:
    adf: dict[str, Any] = {"type": "doc", "content": {}}
    errors = AdfValidator.validate(adf)
    assert any("list" in e for e in errors)


def test_validate_fails_missing_extension_type(adf: dict[str, Any]) -> None:
    bad = copy.deepcopy(adf)
    ext = _find_extension(bad)
    del ext["attrs"]["extensionType"]
    errors = AdfValidator.validate(bad)
    assert any("extensionType" in e for e in errors)


def test_validate_fails_missing_extension_key(adf: dict[str, Any]) -> None:
    bad = copy.deepcopy(adf)
    ext = _find_extension(bad)
    del ext["attrs"]["extensionKey"]
    errors = AdfValidator.validate(bad)
    assert any("extensionKey" in e for e in errors)


def test_validate_fails_empty_mention_id(adf: dict[str, Any]) -> None:
    bad = copy.deepcopy(adf)
    mention = _find_node(bad, "mention")
    mention["attrs"]["id"] = ""
    errors = AdfValidator.validate(bad)
    assert any("mention" in e for e in errors)


def test_validate_fails_bad_tablerow_child() -> None:
    adf: dict[str, Any] = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [{"type": "paragraph", "content": []}],
                    }
                ],
            }
        ],
    }
    errors = AdfValidator.validate(adf)
    assert any("tableRow" in e for e in errors)


def test_validate_accepts_tableheader_in_tablerow(adf: dict[str, Any]) -> None:
    errors = AdfValidator.validate(adf)
    assert errors == []


def _find_extension(adf: dict[str, Any]) -> dict[str, Any]:
    return _find_node(adf, "extension")


def _find_node(node: dict[str, Any] | list[Any], target_type: str) -> dict[str, Any]:
    if isinstance(node, dict):
        if node.get("type") == target_type:
            return node
        for v in node.values():
            result = _find_node(v, target_type)
            if result:
                return result
    elif isinstance(node, list):
        for item in node:
            result = _find_node(item, target_type)
            if result:
                return result
    return {}
