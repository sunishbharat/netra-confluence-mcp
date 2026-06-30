from __future__ import annotations

import re

from confluence.adf.walker import AdfWalker
from models.adf import JqlInfo, PageJqlInspection

_LOCATION_LABELS: dict[str, str] = {
    "table": "table",
    "tableRow": "row",
    "tableCell": "cell",
    "tableHeader": "cell",
    "layoutSection": "section",
    "layoutColumn": "column",
    "panel": "panel",
    "expand": "expand",
    "nestedExpand": "nestedExpand",
    "bulletList": "list",
    "orderedList": "list",
    "listItem": "item",
    "paragraph": "paragraph",
    "heading": "heading",
    "codeBlock": "codeBlock",
    "blockquote": "blockquote",
    "mediaSingle": "mediaSingle",
}

_JQL_KEYWORDS: frozenset[str] = frozenset({
    "AND", "OR", "NOT", "IN", "IS", "WAS", "BY", "EMPTY", "NULL",
    "ORDER", "ASC", "DESC", "ON", "BEFORE", "AFTER",
})


class AdfInspector:
    @staticmethod
    def extract_jql_queries(adf: dict) -> list[JqlInfo]:  # type: ignore[type-arg]
        """
        Walk ADF tree and collect all Jira extension nodes.

        ADF path (deterministic, no regex needed):
            node.type == "extension"
            + node.attrs.extensionKey == "jira"
            -> node.attrs.parameters.macroParams.jqlQuery.value
        """
        nodes = AdfWalker.collect_nodes(
            adf,
            lambda n: (
                n.get("type") == "extension"
                and n.get("attrs", {}).get("extensionKey") == "jira"
            ),
        )

        results: list[JqlInfo] = []
        for node, path in nodes:
            attrs = node.get("attrs", {})
            params = attrs.get("parameters", {})
            macro_params = params.get("macroParams", {})
            macro_metadata = params.get("macroMetadata", {})

            jql_entry = macro_params.get("jqlQuery", {})
            jql = jql_entry.get("value", "") if isinstance(jql_entry, dict) else ""

            if not jql:
                continue

            macro_id_entry = macro_metadata.get("macroId", {})
            macro_id = (
                macro_id_entry.get("value", "")
                if isinstance(macro_id_entry, dict)
                else ""
            )

            columns_entry = macro_params.get("columns", {})
            server_entry = macro_params.get("server", {})
            max_issues_entry = macro_params.get("maximumIssues", {})

            columns_val = (
                columns_entry.get("value", "") if isinstance(columns_entry, dict) else ""
            )
            server_val = (
                server_entry.get("value", "") if isinstance(server_entry, dict) else ""
            )
            max_issues_val = (
                max_issues_entry.get("value", "") if isinstance(max_issues_entry, dict) else ""
            )

            results.append(
                JqlInfo(
                    macro_id=macro_id,
                    location_path=AdfInspector._build_location_path(adf, path),
                    jql=jql,
                    columns=columns_val,
                    server=server_val,
                    max_issues=max_issues_val,
                )
            )

        return results

    @staticmethod
    def build_inspection(page_id: str, title: str, adf: dict) -> PageJqlInspection:  # type: ignore[type-arg]
        """Build a complete PageJqlInspection from a page's ADF."""
        jql_queries = AdfInspector.extract_jql_queries(adf)
        return PageJqlInspection(
            page_id=page_id,
            title=title,
            jira_macro_count=len(jql_queries),
            jql_queries=jql_queries,
            unique_strings=AdfInspector._extract_unique_strings(jql_queries),
        )

    @staticmethod
    def _build_location_path(adf: dict, path: list[str]) -> str:  # type: ignore[type-arg]
        parts: list[str] = []
        current: object = adf

        for segment in path:
            if isinstance(current, list):
                try:
                    idx = int(segment)
                    item = current[idx]
                    if isinstance(item, dict):
                        node_type = item.get("type", "")
                        label = _LOCATION_LABELS.get(node_type)
                        if label:
                            parts.append(f"{label}[{idx}]")
                    current = item
                except (ValueError, IndexError):
                    break
            elif isinstance(current, dict):
                current = current.get(segment)
                if current is None:
                    break
            else:
                break

        return "/".join(parts) if parts else "doc"

    @staticmethod
    def _extract_unique_strings(jql_queries: list[JqlInfo]) -> list[str]:
        tokens: set[str] = set()
        for entry in jql_queries:
            for raw in re.split(r'[\s=!<>~()\[\],\"\']+', entry.jql):
                token = raw.strip()
                if not token:
                    continue
                if token.upper() in _JQL_KEYWORDS:
                    continue
                if token.isdigit():
                    continue
                tokens.add(token)
        return sorted(tokens)
