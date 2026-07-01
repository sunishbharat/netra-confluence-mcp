from __future__ import annotations

from collections.abc import Callable

from models.types import AdfNode


class AdfWalker:
    @staticmethod
    def walk(
        node: AdfNode | list[AdfNode],
        visitor: Callable[[AdfNode, list[str]], None],
        path: list[str] | None = None,
    ) -> None:
        """Call visitor(node, path) for every dict node in the tree."""
        if path is None:
            path = []
        if isinstance(node, dict):
            visitor(node, path)
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    AdfWalker.walk(value, visitor, [*path, key])
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, (dict, list)):
                    AdfWalker.walk(item, visitor, [*path, str(i)])

    @staticmethod
    def collect_nodes(
        adf: AdfNode,
        predicate: Callable[[AdfNode], bool],
    ) -> list[tuple[AdfNode, list[str]]]:
        """Return all nodes matching predicate, with their path lists."""
        results: list[tuple[AdfNode, list[str]]] = []

        def _visitor(node: AdfNode, path: list[str]) -> None:
            if predicate(node):
                results.append((node, path[:]))

        AdfWalker.walk(adf, _visitor)
        return results
