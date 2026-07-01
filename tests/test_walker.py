from __future__ import annotations

from typing import Any

from confluence.adf.walker import AdfWalker


def _simple_adf() -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "hello"},
                ],
            }
        ],
    }


def test_walk_visits_root_node() -> None:
    visited: list[str] = []

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        if node.get("type") == "doc":
            visited.append("doc")

    AdfWalker.walk(_simple_adf(), visitor)
    assert "doc" in visited


def test_walk_visits_nested_nodes() -> None:
    types: list[str] = []

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        t = node.get("type")
        if t:
            types.append(t)

    AdfWalker.walk(_simple_adf(), visitor)
    assert "doc" in types
    assert "paragraph" in types
    assert "text" in types


def test_walk_path_for_paragraph() -> None:
    paths: dict[str, list[str]] = {}

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        t = node.get("type")
        if t:
            paths[t] = path[:]

    AdfWalker.walk(_simple_adf(), visitor)
    assert paths["paragraph"] == ["content", "0"]
    assert paths["text"] == ["content", "0", "content", "0"]


def test_walk_traverses_list_elements() -> None:
    adf = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": []},
            {"type": "paragraph", "content": []},
        ],
    }
    count = 0

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        nonlocal count
        if node.get("type") == "paragraph":
            count += 1

    AdfWalker.walk(adf, visitor)
    assert count == 2


def test_walk_does_not_modify_adf() -> None:
    import copy

    adf = _simple_adf()
    original = copy.deepcopy(adf)
    AdfWalker.walk(adf, lambda n, p: None)
    assert adf == original


def test_collect_nodes_finds_by_type() -> None:
    adf = _simple_adf()
    results = AdfWalker.collect_nodes(adf, lambda n: n.get("type") == "text")
    assert len(results) == 1
    node, path = results[0]
    assert node["text"] == "hello"


def test_collect_nodes_path_is_correct() -> None:
    adf = _simple_adf()
    results = AdfWalker.collect_nodes(adf, lambda n: n.get("type") == "text")
    _, path = results[0]
    assert path == ["content", "0", "content", "0"]


def test_collect_nodes_returns_empty_for_no_match() -> None:
    adf = _simple_adf()
    results = AdfWalker.collect_nodes(adf, lambda n: n.get("type") == "nonexistent")
    assert results == []


def test_collect_nodes_path_is_independent_copy() -> None:
    adf = _simple_adf()
    results = AdfWalker.collect_nodes(adf, lambda n: n.get("type") == "text")
    _, path = results[0]
    path.append("mutated")
    results2 = AdfWalker.collect_nodes(adf, lambda n: n.get("type") == "text")
    _, path2 = results2[0]
    assert "mutated" not in path2


def test_walk_accepts_list_as_root() -> None:
    items = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    types: list[str] = []
    AdfWalker.walk(items, lambda n, p: types.append(n.get("type", "")))
    assert types.count("text") == 2
