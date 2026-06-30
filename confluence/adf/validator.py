from __future__ import annotations

from confluence.adf.walker import AdfWalker


class AdfValidator:
    @staticmethod
    def validate(adf: dict) -> list[str]:  # type: ignore[type-arg]
        """Structural checks on ADF. Returns a list of error strings (empty = valid)."""
        errors: list[str] = []

        if adf.get("type") != "doc":
            errors.append(f"Root node type must be 'doc', got '{adf.get('type')}'")

        if not isinstance(adf.get("content"), list):
            errors.append("Root node 'content' must be a list")

        def visitor(node: dict, path: list[str]) -> None:  # type: ignore[type-arg]
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

    @staticmethod
    def count_replacements(
        adf: dict, title: str, search_term: str  # type: ignore[type-arg]
    ) -> dict[str, int]:
        """Count occurrences of search_term by location type for dry-run preview."""
        counts: dict[str, int] = {"title": 0, "text": 0, "jql": 0, "macro_params": 0}

        counts["title"] = title.count(search_term)

        def visitor(node: dict, path: list[str]) -> None:  # type: ignore[type-arg]
            node_type = node.get("type", "")

            if node_type == "text":
                text = node.get("text", "")
                if isinstance(text, str):
                    counts["text"] += text.count(search_term)

            if node_type == "extension":
                attrs = node.get("attrs", {})
                params = attrs.get("parameters", {})
                macro_params = params.get("macroParams", {})

                jql_entry = macro_params.get("jqlQuery", {})
                if isinstance(jql_entry, dict):
                    jql = jql_entry.get("value", "")
                    if isinstance(jql, str):
                        counts["jql"] += jql.count(search_term)

                for param_key, param_val in macro_params.items():
                    if param_key == "jqlQuery":
                        continue
                    if isinstance(param_val, dict):
                        val = param_val.get("value", "")
                        if isinstance(val, str):
                            counts["macro_params"] += val.count(search_term)

        AdfWalker.walk(adf, visitor)
        return counts
