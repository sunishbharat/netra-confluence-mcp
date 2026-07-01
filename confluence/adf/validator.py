from __future__ import annotations

from confluence.adf.walker import AdfWalker
from models.types import AdfNode


class AdfValidator:
    @staticmethod
    def validate(adf: AdfNode) -> list[str]:
        """Structural checks on ADF. Returns a list of error strings (empty = valid)."""
        errors: list[str] = []

        if adf.get("type") != "doc":
            errors.append(f"Root node type must be 'doc', got '{adf.get('type')}'")

        if not isinstance(adf.get("content"), list):
            errors.append("Root node 'content' must be a list")

        def visitor(node: AdfNode, path: list[str]) -> None:
            node_type = node.get("type", "")
            loc = "/".join(path) or "root"

            if node_type == "extension":
                attrs = node.get("attrs", {})
                if not attrs.get("extensionType"):
                    errors.append(f"extension node at {loc} missing 'extensionType'")
                if not attrs.get("extensionKey"):
                    errors.append(f"extension node at {loc} missing 'extensionKey'")

            if node_type == "mention":
                attrs = node.get("attrs", {})
                if not attrs.get("id"):
                    errors.append(f"mention node at {loc} has empty 'attrs.id'")

            if node_type == "tableRow":
                children = node.get("content", [])
                for i, child in enumerate(children):
                    if isinstance(child, dict) and child.get("type") not in (
                        "tableCell",
                        "tableHeader",
                    ):
                        errors.append(
                            f"tableRow at {loc} has invalid child type"
                            f" '{child.get('type')}' at index {i}"
                        )

        AdfWalker.walk(adf, visitor)
        return errors
