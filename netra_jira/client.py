from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from exceptions import (
    JiraAPIError,
    JiraIssueNotFoundError,
    JiraNetworkError,
    JiraPermissionError,
    JiraRateLimitedError,
    MissingJiraCredentialsError,
)

log = structlog.get_logger()

# One initial attempt plus two retries: enough to ride out a short Atlassian rate-limit
# window without letting a single tool call hang for minutes.
_MAX_ATTEMPTS = 3

# Upper bound on any single wait, including a server-sent Retry-After, so a misbehaving
# header can never stall a tool call indefinitely.
_MAX_WAIT_SECONDS = 60.0

# Full-jitter exponential backoff (coding guidelines: base 1s, x2, capped at 60s) for
# retries where Jira sent no Retry-After; jitter avoids synchronized retries from
# parallel link/search calls hitting the same rate limit.
_BACKOFF = wait_random_exponential(multiplier=1, max=_MAX_WAIT_SECONDS)

SleepFn = Callable[[float], Awaitable[None]]


def describe_error_body(body: object) -> str:
    """Flatten a Jira error body ({errorMessages: [...], errors: {field: msg}}) to one line."""
    if not isinstance(body, dict):
        return str(body)
    parts: list[str] = []
    messages = body.get("errorMessages")
    if isinstance(messages, list):
        parts.extend(str(m) for m in messages if m)
    errors = body.get("errors")
    if isinstance(errors, dict):
        parts.extend(f"{field}: {msg}" for field, msg in errors.items())
    return "; ".join(parts) if parts else str(body)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header given either as delta-seconds or as an HTTP date."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, JiraRateLimitedError) and exc.retry_after is not None:
        return min(exc.retry_after, _MAX_WAIT_SECONDS)
    return float(_BACKOFF(retry_state))


class JiraClient:
    """Thin async Jira Cloud REST client bound to one user's identity.

    Mirrors ConfluenceClient: BasicAuth on httpx.AsyncClient, typed exceptions per
    HTTP status, and `async with` lifecycle so per-request clients are always closed.
    Adds a retry policy: HTTP 429 is retried on every method (a 429 is rejected before
    processing, so a retry cannot duplicate a write), honoring Retry-After; network
    errors are retried only on idempotent calls, because a write whose response was
    lost may already have created issues.
    """

    def __init__(self, base_url: str, site_url: str, email: str, token: str) -> None:
        if not email or not token:
            raise MissingJiraCredentialsError("missing per-user Jira credentials")

        self._site_url = site_url.rstrip("/")
        self._sleep: SleepFn = asyncio.sleep
        self._http = httpx.AsyncClient(
            base_url=base_url,
            auth=httpx.BasicAuth(email, token),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            # Same budget as ConfluenceClient: bulk create payloads of 50 issues with ADF
            # descriptions need more than httpx's 5s default; connect stays short.
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    def browse_url(self, issue_key: str) -> str:
        """Return the browser URL for an issue key."""
        return f"{self._site_url}/browse/{issue_key}"

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
        return await self._send("GET", path, idempotent=True, **kwargs)

    async def post(
        self,
        path: str,
        *,
        idempotent: bool = False,
        allow_status: frozenset[int] = frozenset(),
        **kwargs: Any,  # noqa: ANN401
    ) -> httpx.Response:
        """POST; pass idempotent=True only for read-style POSTs such as JQL search."""
        return await self._send(
            "POST", path, idempotent=idempotent, allow_status=allow_status, **kwargs
        )

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
        # A field-set PUT is idempotent in effect, but it is still a write: keep it out of
        # network-error retries so a lost response is reported, not silently re-sent.
        return await self._send("PUT", path, idempotent=False, **kwargs)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        idempotent: bool,
        allow_status: frozenset[int] = frozenset(),
        **kwargs: Any,  # noqa: ANN401
    ) -> httpx.Response:
        retryable: tuple[type[BaseException], ...] = (
            (JiraRateLimitedError, JiraNetworkError) if idempotent else (JiraRateLimitedError,)
        )

        def log_retry(retry_state: RetryCallState) -> None:
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            wait = retry_state.next_action.sleep if retry_state.next_action else None
            log.warning(
                "jira_request_retry",
                method=method,
                path=path,
                attempt=retry_state.attempt_number,
                wait_seconds=wait,
                error=str(exc),
            )

        retrying = AsyncRetrying(
            stop=stop_after_attempt(_MAX_ATTEMPTS),
            wait=_wait,
            retry=retry_if_exception_type(retryable),
            reraise=True,
            sleep=self._sleep,
            before_sleep=log_retry,
        )
        async for attempt in retrying:
            with attempt:
                response = await self._request(method, path, **kwargs)
                self._raise_for_status(response, allow_status)
                return response
        raise JiraAPIError(f"Retry loop for {method} {path} exited without a result")

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise JiraNetworkError(f"Network error calling Jira ({method} {path}): {e}") from e

    def _raise_for_status(self, response: httpx.Response, allow_status: frozenset[int]) -> None:
        status = response.status_code
        if status in allow_status or not response.is_error:
            return
        detail = self._error_detail(response)
        if status == 403:
            raise JiraPermissionError(f"Permission denied (HTTP 403): {response.url}: {detail}")
        if status == 404:
            raise JiraIssueNotFoundError(f"Not found (HTTP 404): {response.url}: {detail}")
        if status == 429:
            raise JiraRateLimitedError(
                f"Rate limited by Jira (HTTP 429): {response.url}",
                retry_after=_parse_retry_after(response.headers.get("Retry-After")),
            )
        raise JiraAPIError(f"Jira API error HTTP {status}: {detail}")

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            return describe_error_body(response.json())
        except ValueError:
            return response.text

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> JiraClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()
