from __future__ import annotations

from collections import defaultdict

from models.adf import ChangeLogEntry


class AdfDiffer:
    @staticmethod
    def summarize_changes(change_log: list[ChangeLogEntry]) -> str:
        """Group change log entries by location prefix and return a human-readable summary."""
        if not change_log:
            return "No changes"

        groups: dict[str, int] = defaultdict(int)
        for entry in change_log:
            prefix = entry.location.split(":")[0]
            groups[prefix] += 1

        lines = [f"{prefix}: {count} change(s)" for prefix, count in sorted(groups.items())]
        return "\n".join(lines)
