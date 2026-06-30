from __future__ import annotations

import copy
import uuid

from confluence.adf.walker import AdfWalker
from models.adf import ChangeLogEntry, ReplacementRule


class AdfReplacer:
    def __init__(self, rules: list[ReplacementRule]) -> None:
        self._rules = sorted(rules, key=lambda r: len(r.old), reverse=True)

    def apply(
        self, adf: dict, title: str  # type: ignore[type-arg]
    ) -> tuple[dict, str, list[ChangeLogEntry]]:  # type: ignore[type-arg]
        """
        Returns (new_adf, new_title, change_log).

        Always works on a deep copy - the caller's original ADF is never mutated.
        """
        new_adf: dict = copy.deepcopy(adf)  # type: ignore[type-arg]
        new_title = title
        change_log: list[ChangeLogEntry] = []

        for rule in self._rules:
            if rule.scope in ("all", "title") and rule.old in new_title:
                new_title = new_title.replace(rule.old, rule.new)
                change_log.append(
                    ChangeLogEntry(location="title", detail=f"'{rule.old}' -> '{rule.new}'")
                )

            self._apply_rule_to_adf(new_adf, rule, change_log)

        if any(r.regenerate_macro_ids for r in self._rules):
            self._regenerate_macro_ids(new_adf)

        return new_adf, new_title, change_log

    def _apply_rule_to_adf(
        self,
        adf: dict,  # type: ignore[type-arg]
        rule: ReplacementRule,
        change_log: list[ChangeLogEntry],
    ) -> None:
        def visitor(node: dict, path: list[str]) -> None:  # type: ignore[type-arg]
            node_type = node.get("type", "")

            if node_type == "mention":
                return

            if node_type == "text" and rule.scope in ("all", "text"):
                text = node.get("text", "")
                if isinstance(text, str) and rule.old in text:
                    node["text"] = text.replace(rule.old, rule.new)
                    change_log.append(
                        ChangeLogEntry(
                            location=f"text:{'/'.join(path)}",
                            detail=f"'{rule.old}' -> '{rule.new}'",
                        )
                    )

            if node_type == "extension":
                attrs = node.get("attrs", {})
                params = attrs.get("parameters", {})
                macro_params = params.get("macroParams", {})

                if rule.scope in ("all", "macro_params", "jql"):
                    jql_param = macro_params.get("jqlQuery", {})
                    if isinstance(jql_param, dict):
                        val = jql_param.get("value", "")
                        if isinstance(val, str) and rule.old in val:
                            jql_param["value"] = val.replace(rule.old, rule.new)
                            change_log.append(
                                ChangeLogEntry(
                                    location=f"jql:{'/'.join(path)}",
                                    detail=f"'{rule.old}' -> '{rule.new}'",
                                )
                            )

                if rule.scope in ("all", "macro_params"):
                    for param_key, param_val in macro_params.items():
                        if param_key == "jqlQuery":
                            continue
                        if isinstance(param_val, dict):
                            val = param_val.get("value", "")
                            if isinstance(val, str) and rule.old in val:
                                param_val["value"] = val.replace(rule.old, rule.new)
                                change_log.append(
                                    ChangeLogEntry(
                                        location=f"macro_param:{param_key}:{'/'.join(path)}",
                                        detail=f"'{rule.old}' -> '{rule.new}'",
                                    )
                                )

        AdfWalker.walk(adf, visitor)

    @staticmethod
    def _regenerate_macro_ids(adf: dict) -> None:  # type: ignore[type-arg]
        def visitor(node: dict, path: list[str]) -> None:  # type: ignore[type-arg]
            if node.get("type") == "extension":
                attrs = node.get("attrs", {})
                params = attrs.get("parameters", {})
                meta = params.get("macroMetadata", {})
                macro_id = meta.get("macroId", {})
                if isinstance(macro_id, dict) and "value" in macro_id:
                    macro_id["value"] = str(uuid.uuid4())

        AdfWalker.walk(adf, visitor)
