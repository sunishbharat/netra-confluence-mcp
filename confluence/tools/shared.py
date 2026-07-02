from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from fastmcp.server.dependencies import get_http_headers
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from confluence.adf.differ import AdfDiffer
from confluence.adf.validator import AdfValidator
from confluence.adf.walker import AdfWalker
from confluence.api import read_page, update_page
from confluence.client import ConfluenceClient
from exceptions import AdfValidationError, MissingCredentialsError, VersionConflictError
from models.adf import ChangeLogEntry, DryRunResult
from models.config import NetraSettings
from models.confluence import PageMetadata, UpdatePageRequest

log = structlog.get_logger()

TransformFn = Callable[[dict[str, Any], str], tuple[dict[str, Any], str, list[ChangeLogEntry]]]

_HEADER_EMAIL = "x-confluence-user-email"
_HEADER_TOKEN = "x-confluence-api-token"


def get_client() -> ConfluenceClient:
    """Build a ConfluenceClient scoped to the identity of the calling human.

    stdio transport: one server process per user already (Tier 0), so
    credentials come from that user's own environment/.env, unchanged from
    before Tier 1.

    http transport: the server owns no Confluence identity. Every call must
    carry X-Confluence-User-Email / X-Confluence-Api-Token headers; a fresh
    client is built from them so Confluence attribution and permissions match
    the human who triggered the call, never a shared service account. There
    is no fallback to env vars here - a missing header is a hard error.

    Callers must use this as `async with get_client() as client:` so the
    per-request client (and its underlying connection) is always closed.
    """
    settings = NetraSettings()  # type: ignore[call-arg]  # fields read from env

    if settings.server_transport != "http":
        return ConfluenceClient(settings)

    headers = get_http_headers()
    email = headers.get(_HEADER_EMAIL, "").strip()
    token = headers.get(_HEADER_TOKEN, "").strip()
    if not email or not token:
        raise MissingCredentialsError("missing per-user Confluence credentials")

    per_user_settings = NetraSettings(  # type: ignore[call-arg]  # base_url/site_url read from env
        confluence_api_token=token,
        confluence_user_email=email,
    )
    return ConfluenceClient(per_user_settings)


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
) -> tuple[PageMetadata | None, list[ChangeLogEntry]]:
    """Read-transform-validate-write with automatic retry on 409 version conflicts.

    transform_fn returns (new_adf, new_title, change_log). On a 409, the
    exception propagates so tenacity retries with a fresh read - and the ADF
    produced by that fresh read is validated again here on every attempt, not
    just once against a throwaway preview. This is the data that is actually
    about to be written, which matters most on a retry: a 409 means someone
    else just edited the page, so the freshly re-read content is exactly the
    content least likely to match what a caller previewed earlier.

    Raises AdfValidationError (never writes) if validation fails. Returns
    (None, []) without writing if transform_fn produces no changes.
    """
    page = await read_page(client, page_id)
    new_adf, new_title, change_log = transform_fn(page.adf, page.title)

    if not change_log:
        return None, []

    errors = AdfValidator.validate(new_adf)
    if errors:
        raise AdfValidationError(errors)

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


def build_no_changes_response(page_id: str, title: str | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"status": "NO_CHANGES", "page_id": page_id}
    if title is not None:
        response["title"] = title
    return response


def build_dry_run_response(
    current_title: str,
    new_title: str,
    current_version: int,
    change_log: list[ChangeLogEntry],
) -> dict[str, Any]:
    result = DryRunResult(
        current_title=current_title,
        new_title=new_title,
        current_version=current_version,
        total_changes=len(change_log),
        change_summary=AdfDiffer.summarize_changes(change_log),
        change_log=change_log,
    )
    return result.model_dump()


def build_updated_response(
    client: ConfluenceClient, meta: PageMetadata, applied_log: list[ChangeLogEntry]
) -> dict[str, Any]:
    return {
        "status": "UPDATED",
        "page_id": meta.id,
        "title": meta.title,
        "version": meta.version,
        "url": f"{client.site_url}/wiki/spaces/{meta.space_id}/pages/{meta.id}",
        "total_changes": len(applied_log),
        "change_summary": AdfDiffer.summarize_changes(applied_log),
    }


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
