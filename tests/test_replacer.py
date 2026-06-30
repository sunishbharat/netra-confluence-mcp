from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from confluence.adf.replacer import AdfReplacer
from models.adf import ReplacementRule

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_adf.json"


@pytest.fixture
def adf() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text()))


def _rule(
    old: str,
    new: str,
    scope: Literal["all", "text", "macro_params", "jql", "title"] = "all",
    regen: bool = False,
) -> ReplacementRule:
    return ReplacementRule(old=old, new=new, scope=scope, regenerate_macro_ids=regen)


def test_deep_copy_immutability(adf: dict[str, Any]) -> None:
    original = copy.deepcopy(adf)
    replacer = AdfReplacer([_rule("R1.0", "R2.0")])
    replacer.apply(adf, "R1.0 Report")
    assert adf == original


def test_apply_returns_new_adf_object(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0")])
    new_adf, _, _ = replacer.apply(adf, "title")
    assert new_adf is not adf


def _columns_values(adf_node: dict[str, Any]) -> list[str]:
    """Collect every 'columns' macro param value from the ADF."""
    from confluence.adf.walker import AdfWalker

    values: list[str] = []

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        if node.get("type") != "extension":
            return
        cols = node.get("attrs", {}).get("parameters", {}).get("macroParams", {}).get("columns", {})
        if isinstance(cols, dict):
            values.append(str(cols.get("value", "")))

    AdfWalker.walk(adf_node, visitor)
    return values


def _jql_values(adf_node: dict[str, Any]) -> list[str]:
    """Collect every 'jqlQuery' macro param value from the ADF."""
    from confluence.adf.walker import AdfWalker

    values: list[str] = []

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        if node.get("type") != "extension":
            return
        jql = node.get("attrs", {}).get("parameters", {}).get("macroParams", {}).get("jqlQuery", {})
        if isinstance(jql, dict):
            values.append(str(jql.get("value", "")))

    AdfWalker.walk(adf_node, visitor)
    return values


def test_scope_jql_replaces_jql_not_columns(adf: dict[str, Any]) -> None:
    original_columns = _columns_values(adf)
    original_jqls = _jql_values(adf)
    assert original_columns, "fixture must have at least one columns param to test against"
    assert any("R1.0" in c for c in original_columns), (
        "fixture columns must contain R1.0 for the assertion to be meaningful"
    )

    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="jql")])
    new_adf, _, log = replacer.apply(adf, "R1.0 Report")

    # Every change must be a jql change - no text, title, or macro_param entries.
    assert log, "expected at least one jql change"
    for entry in log:
        assert entry.location.startswith("jql:"), (
            f"scope='jql' produced non-jql change at {entry.location}"
        )

    # Columns param values must be byte-identical to the original.
    assert _columns_values(new_adf) == original_columns, (
        "scope='jql' must not touch the columns param values"
    )

    # JQL values that contained R1.0 must now contain R2.0 instead.
    for original_jql, new_jql in zip(original_jqls, _jql_values(new_adf), strict=True):
        if "R1.0" in original_jql:
            assert "R1.0" not in new_jql, f"jql still contains R1.0 after replace: {new_jql}"
            assert "R2.0" in new_jql, f"jql missing R2.0 after replace: {new_jql}"


def test_scope_jql_does_not_touch_title(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="jql")])
    _, new_title, _ = replacer.apply(adf, "R1.0 Report")
    assert new_title == "R1.0 Report"


def test_scope_macro_params_replaces_jql_and_columns(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="macro_params")])
    _, _, log = replacer.apply(adf, "title")
    locations = [e.location for e in log]
    assert any("jql:" in loc for loc in locations)
    assert any("macro_param:columns" in loc for loc in locations)


def test_scope_macro_params_does_not_touch_text(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="macro_params")])
    _, _, log = replacer.apply(adf, "title")
    assert not any("text:" in e.location for e in log)


def test_scope_text_replaces_text_nodes_only(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="text")])
    _, _, log = replacer.apply(adf, "title")
    for entry in log:
        assert entry.location.startswith("text:")


def test_scope_all_replaces_title_text_jql_and_params(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="all")])
    _, new_title, log = replacer.apply(adf, "R1.0 Report")
    assert new_title == "R2.0 Report"
    locations = [e.location for e in log]
    assert any("jql:" in loc for loc in locations)
    assert any("text:" in loc for loc in locations)


def test_scope_title_replaces_only_title(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="title")])
    _, new_title, log = replacer.apply(adf, "R1.0 Report")
    assert new_title == "R2.0 Report"
    assert all(e.location == "title" for e in log)


def test_longest_match_first_prevents_partial_corruption(adf: dict[str, Any]) -> None:
    rules = [
        _rule("ver_R1.0", "ver_R2.0", scope="jql"),
        _rule("ver_R1.0_Baseline", "ver_R2.0_Baseline", scope="jql"),
    ]
    replacer = AdfReplacer(rules)
    new_adf, _, _ = replacer.apply(adf, "title")
    adf_str = json.dumps(new_adf)
    assert "ver_R2.0_Baseline" in adf_str
    assert "ver_R2.0_Baseline".replace("Baseline", "") + "Baseline" not in adf_str.replace(
        "ver_R2.0_Baseline", ""
    )


def test_rules_sorted_longest_first() -> None:
    rules = [
        ReplacementRule(old="abc", new="x"),
        ReplacementRule(old="abcdef", new="y"),
        ReplacementRule(old="ab", new="z"),
    ]
    replacer = AdfReplacer(rules)
    assert replacer._rules[0].old == "abcdef"
    assert replacer._rules[1].old == "abc"
    assert replacer._rules[2].old == "ab"


def test_mention_node_attrs_never_touched(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("user-account-abc123", "hacked", scope="all")])
    new_adf, _, _ = replacer.apply(adf, "title")
    adf_str = json.dumps(new_adf)
    assert "user-account-abc123" in adf_str


def test_macro_id_preserved_without_regen_flag(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="all", regen=False)])
    new_adf, _, _ = replacer.apply(adf, "title")
    adf_str = json.dumps(new_adf)
    assert "macro-id-001" in adf_str
    assert "macro-id-002" in adf_str


def test_macro_id_regenerated_with_regen_flag(adf: dict[str, Any]) -> None:
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="all", regen=True)])
    new_adf, _, _ = replacer.apply(adf, "title")
    adf_str = json.dumps(new_adf)
    assert "macro-id-001" not in adf_str
    assert "macro-id-002" not in adf_str
    new_ids = uuid_pattern.findall(adf_str)
    assert len(new_ids) >= 2


def test_no_match_returns_empty_change_log(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("NOMATCH_XYZ", "anything")])
    _, _, log = replacer.apply(adf, "title")
    assert log == []


def test_change_log_contains_detail(adf: dict[str, Any]) -> None:
    replacer = AdfReplacer([_rule("R1.0", "R2.0", scope="jql")])
    _, _, log = replacer.apply(adf, "title")
    for entry in log:
        assert "'R1.0' -> 'R2.0'" in entry.detail


def test_empty_rule_list_is_no_op(adf: dict[str, Any]) -> None:
    original = copy.deepcopy(adf)
    replacer = AdfReplacer([])
    new_adf, new_title, log = replacer.apply(adf, "original title")
    assert new_adf == original
    assert new_title == "original title"
    assert log == []
