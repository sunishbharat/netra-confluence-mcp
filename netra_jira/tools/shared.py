from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastmcp.server.dependencies import get_http_headers

from exceptions import MissingJiraCredentialsError, NetraJiraError
from models.config import NetraSettings
from models.jira import BulkItemResult, FieldMeta, IssueTypeMeta
from netra_jira.api import bulk_create_issues, create_clone_link, find_existing_summaries
from netra_jira.client import JiraClient
from netra_jira.fields import missing_required_fields

log = structlog.get_logger()

_HEADER_JIRA_EMAIL = "x-jira-user-email"
_HEADER_JIRA_TOKEN = "x-jira-api-token"
_HEADER_CONFLUENCE_EMAIL = "x-confluence-user-email"
_HEADER_CONFLUENCE_TOKEN = "x-confluence-api-token"

# Concurrent clone-link calls after a bulk create; bounded for the same rate-limit reason
# as the duplicate-check searches.
_LINK_CONCURRENCY = 5

_MISSING_CREDENTIALS = "missing per-user Jira credentials"


def _pick_pair(
    jira_email: str | None,
    jira_token: str | None,
    confluence_email: str | None,
    confluence_token: str | None,
) -> tuple[str, str]:
    """Choose one complete credential pair: Jira's if any Jira value is given, else Confluence's.

    Pairs are never mixed (Jira email + Confluence token): a half-configured Jira pair
    is an error, not a silent blend of two credentials.
    """
    if jira_email or jira_token:
        if not jira_email or not jira_token:
            raise MissingJiraCredentialsError(
                f"{_MISSING_CREDENTIALS}: Jira email and API token must be given together"
            )
        return jira_email, jira_token
    if confluence_email and confluence_token:
        return confluence_email, confluence_token
    raise MissingJiraCredentialsError(_MISSING_CREDENTIALS)


def get_jira_client() -> JiraClient:
    """Build a JiraClient scoped to the identity of the calling human.

    stdio transport: JIRA_USER_EMAIL / JIRA_API_TOKEN from the user's own env, else
    CONFLUENCE_USER_EMAIL / CONFLUENCE_API_TOKEN (same person, same Atlassian site).

    http transport: X-Jira-User-Email / X-Jira-Api-Token headers, else the
    X-Confluence-* headers of the same request. Env credentials are never read on
    http - a missing header pair is a hard error, never a shared-identity fallback.

    Callers must use `async with get_jira_client() as client:`.
    """
    settings = NetraSettings()  # type: ignore[call-arg]  # fields read from env

    if settings.server_transport != "http":
        email, token = _pick_pair(
            settings.jira_user_email,
            settings.jira_api_token,
            settings.confluence_user_email,
            settings.confluence_api_token,
        )
    else:
        headers = get_http_headers()
        email, token = _pick_pair(
            headers.get(_HEADER_JIRA_EMAIL, "").strip(),
            headers.get(_HEADER_JIRA_TOKEN, "").strip(),
            headers.get(_HEADER_CONFLUENCE_EMAIL, "").strip(),
            headers.get(_HEADER_CONFLUENCE_TOKEN, "").strip(),
        )

    return JiraClient(
        base_url=settings.confluence_base_url,
        site_url=settings.confluence_site_url,
        email=email,
        token=token,
    )


def find_issue_type(issue_types: list[IssueTypeMeta], name: str) -> IssueTypeMeta | None:
    """Case-insensitive exact match on issue type name."""
    wanted = name.casefold()
    for issue_type in issue_types:
        if issue_type.name.casefold() == wanted:
            return issue_type
    return None


@dataclass(frozen=True)
class PreparedItem:
    """One issue ready to send to the bulk endpoint."""

    index: int
    summary: str
    fields: dict[str, Any]
    overrides: list[str] = field(default_factory=list)


def required_field_errors(items: list[PreparedItem], create_fields: list[FieldMeta]) -> list[str]:
    """One error per item that is missing a required field with no default."""
    errors: list[str] = []
    for item in items:
        missing = missing_required_fields(item.fields, create_fields)
        if missing:
            errors.append(f"item[{item.index}] '{item.summary}': missing required {missing}")
    return errors


def validation_failed(errors: list[str]) -> dict[str, Any]:
    return {"status": "VALIDATION_FAILED", "errors": errors}


def _dump(results: list[BulkItemResult]) -> list[dict[str, Any]]:
    return [r.model_dump(exclude_none=True) for r in results]


def build_bulk_dry_run(
    context: dict[str, Any],
    items: list[PreparedItem],
    existing: dict[str, str],
) -> dict[str, Any]:
    """DRY_RUN (or NO_CHANGES when every item already exists) listing every item."""
    results: list[BulkItemResult] = []
    for item in items:
        if item.summary in existing:
            results.append(
                BulkItemResult(
                    index=item.index,
                    summary=item.summary,
                    status="SKIPPED_EXISTS",
                    existing_issue_key=existing[item.summary],
                )
            )
        else:
            results.append(
                BulkItemResult(
                    index=item.index,
                    summary=item.summary,
                    status="WILL_CREATE",
                    overrides=item.overrides,
                )
            )
    to_create = sum(1 for r in results if r.status == "WILL_CREATE")
    response: dict[str, Any] = {
        "status": "DRY_RUN" if to_create else "NO_CHANGES",
        **context,
        "total_items": len(items),
        "to_create": to_create,
        "skipped_existing": len(items) - to_create,
        "items": _dump(results),
    }
    if to_create:
        response["message"] = "Preview only. Call again with dry_run=False to create."
    return response


async def _link_all(
    client: JiraClient, created_keys: dict[int, str], template_key: str
) -> dict[int, str]:
    """Create clone links; return {index: error} for links that failed."""
    semaphore = asyncio.Semaphore(_LINK_CONCURRENCY)

    async def link(index: int, key: str) -> tuple[int, str | None]:
        async with semaphore:
            try:
                await create_clone_link(client, key, template_key)
            except NetraJiraError as e:
                log.warning("jira_clone_link_failed", issue_key=key, template=template_key)
                return index, str(e)
            return index, None

    outcomes = await asyncio.gather(*(link(i, k) for i, k in created_keys.items()))
    return {index: err for index, err in outcomes if err is not None}


async def execute_bulk_create(
    client: JiraClient,
    context: dict[str, Any],
    items: list[PreparedItem],
    existing: dict[str, str],
    link_template_key: str | None = None,
) -> dict[str, Any]:
    """Create the non-skipped items in one bulk call; link them; report per item.

    Status: CREATED (all created), PARTIAL (some failed), ERROR (none created),
    NO_CHANGES (every item already existed).
    """
    to_create = [item for item in items if item.summary not in existing]
    if not to_create:
        # Every item already exists; the dry-run builder reports exactly that as NO_CHANGES.
        return build_bulk_dry_run(context, items, existing)

    bulk = await bulk_create_issues(client, [item.fields for item in to_create])

    created_keys = {
        to_create[pos].index: issue.key
        for pos, issue in bulk.created.items()
        if pos < len(to_create)
    }
    link_errors: dict[int, str] = {}
    if link_template_key and created_keys:
        link_errors = await _link_all(client, created_keys, link_template_key)

    results: list[BulkItemResult] = []
    for pos, item in enumerate(to_create):
        if pos in bulk.created:
            key = bulk.created[pos].key
            results.append(
                BulkItemResult(
                    index=item.index,
                    summary=item.summary,
                    status="CREATED",
                    issue_key=key,
                    url=client.browse_url(key),
                    link_error=link_errors.get(item.index),
                )
            )
        else:
            results.append(
                BulkItemResult(
                    index=item.index,
                    summary=item.summary,
                    status="FAILED",
                    error=bulk.errors.get(pos, "Jira did not report a result for this item"),
                )
            )
    for item in items:
        if item.summary in existing:
            results.append(
                BulkItemResult(
                    index=item.index,
                    summary=item.summary,
                    status="SKIPPED_EXISTS",
                    existing_issue_key=existing[item.summary],
                )
            )
    results.sort(key=lambda r: r.index)

    created = sum(1 for r in results if r.status == "CREATED")
    failed = sum(1 for r in results if r.status == "FAILED")
    skipped = len(items) - len(to_create)
    if failed == 0:
        status = "CREATED"
    elif created:
        status = "PARTIAL"
    else:
        status = "ERROR"

    log.info(
        "jira_bulk_create_finished",
        status=status,
        created=created,
        failed=failed,
        skipped_existing=skipped,
    )
    response: dict[str, Any] = {
        "status": status,
        **context,
        "created": created,
        "failed": failed,
        "skipped_existing": skipped,
        "results": _dump(results),
    }
    if status == "ERROR":
        response["error"] = "No issues were created; see results for each item's error"
    return response


async def find_existing(
    client: JiraClient, project_key: str, items: list[PreparedItem]
) -> dict[str, str]:
    return await find_existing_summaries(client, project_key, [item.summary for item in items])
