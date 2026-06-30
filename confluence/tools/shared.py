from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from confluence.adf.walker import AdfWalker
from confluence.api import read_page, update_page
from confluence.client import ConfluenceClient
from exceptions import VersionConflictError
from models.adf import ChangeLogEntry
from models.config import NetraSettings
from models.confluence import PageMetadata, UpdatePageRequest

log = structlog.get_logger()

_client: ConfluenceClient | None = None

TransformFn = Callable[[dict[str, Any], str], tuple[dict[str, Any], str, list[ChangeLogEntry]]]


def get_client() -> ConfluenceClient:
    global _client
    if _client is None:
        _client = ConfluenceClient(NetraSettings())  # type: ignore[call-arg]  # fields read from env
    return _client


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(VersionConflictError),
    reraise=True,
)
async def safe_update(
    client: ConfluenceClient,
    page_id: str,
    transform_fn: TransformFn,
    version_message: str,
) -> tuple[PageMetadata, list[ChangeLogEntry]]:
    """Read-transform-write with automatic retry on 409 version conflicts.

    transform_fn returns (new_adf, new_title, change_log). On a 409, the
    exception propagates so tenacity retries with a fresh read.
    """
    page = await read_page(client, page_id)
    new_adf, new_title, change_log = transform_fn(page.adf, page.title)
    meta = await update_page(
        client,
        UpdatePageRequest(
            page_id=page_id,
            title=new_title,
            adf_body=new_adf,
            version_number=page.version + 1,
            version_message=version_message,
        ),
    )
    return meta, change_log


def _iso_date_to_ms(date_str: str) -> str:
    """Convert ISO date string (YYYY-MM-DD) to milliseconds since epoch as string."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    return str(int(dt.timestamp() * 1000))


def update_date_nodes(
    adf: dict[str, Any], new_delivery_date: str
) -> tuple[dict[str, Any], list[ChangeLogEntry]]:
    """Update all date node timestamps on a deep copy of the input ADF.

    Returns (new_adf, change_log). The input ADF is never mutated, so this
    helper is safe to call on the original page ADF or on the result of
    AdfReplacer.apply() without violating the deep-copy invariant.
    """
    new_adf: dict[str, Any] = copy.deepcopy(adf)
    new_ts = _iso_date_to_ms(new_delivery_date)
    change_log: list[ChangeLogEntry] = []

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        if node.get("type") != "date":
            return
        attrs = node.get("attrs", {})
        if not isinstance(attrs, dict):
            return
        old_ts = attrs.get("timestamp", "")
        if old_ts != new_ts:
            attrs["timestamp"] = new_ts
            change_log.append(
                ChangeLogEntry(
                    location=f"date:{'/'.join(path)}",
                    detail=f"timestamp '{old_ts}' -> '{new_ts}' ({new_delivery_date})",
                )
            )

    AdfWalker.walk(new_adf, visitor)
    return new_adf, change_log
