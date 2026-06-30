from __future__ import annotations

from collections.abc import Callable


class AdfWalker:
    @staticmethod
    def walk(
        node: dict | list,  # type: ignore[type-arg]
        visitor: Callable[[dict, list[str]], None],  # type: ignore[type-arg]
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
        adf: dict,  # type: ignore[type-arg]
        predicate: Callable[[dict], bool],  # type: ignore[type-arg]
    ) -> list[tuple[dict, list[str]]]:  # type: ignore[type-arg]
        """Return all nodes matching predicate, with their path lists."""
        results: list[tuple[dict, list[str]]] = []  # type: ignore[type-arg]

        def _visitor(node: dict, path: list[str]) -> None:  # type: ignore[type-arg]
            if predicate(node):
                results.append((node, path[:]))

        AdfWalker.walk(adf, _visitor)
        return results
