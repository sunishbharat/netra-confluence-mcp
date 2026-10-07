from __future__ import annotations


class NetraConfluenceError(Exception):
    """Base exception for all Netra Confluence errors."""


class VersionConflictError(NetraConfluenceError):
    """Page was modified between read and write (HTTP 409)."""


class AdfValidationError(NetraConfluenceError):
    """ADF structure is invalid - write blocked."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class ConfluencePermissionError(NetraConfluenceError):
    """Caller lacks the required Confluence permission (HTTP 403)."""


class PageNotFoundError(NetraConfluenceError):
    """Requested page does not exist (HTTP 404)."""


class ConfluenceAPIError(NetraConfluenceError):
    """Unclassified Confluence API error."""


class MissingCredentialsError(NetraConfluenceError):
    """HTTP transport call arrived without per-user Confluence credential headers (401-style).

    Never caught and fall back to a shared identity - that would silently
    reintroduce the service-account attribution problem Tier 1 exists to fix.
    """


class NetraJiraError(Exception):
    """Base exception for all Netra Jira errors.

    Deliberately separate from NetraConfluenceError so Jira and Confluence
    failures can be caught independently at each tool boundary.
    """


class JiraAPIError(NetraJiraError):
    """Unclassified Jira API error."""


class JiraNetworkError(JiraAPIError):
    """No HTTP response was received (connect error, timeout, dropped connection).

    Distinct from JiraAPIError so the client can retry it on idempotent reads
    only: a write whose response was lost may already have been applied.
    """


class JiraIssueNotFoundError(NetraJiraError):
    """Requested issue, project, or issue type does not exist (HTTP 404)."""


class JiraPermissionError(NetraJiraError):
    """Caller lacks the required Jira permission (HTTP 403)."""


class JiraRateLimitedError(NetraJiraError):
    """Jira rate limit hit (HTTP 429).

    retry_after carries the parsed Retry-After header in seconds, or None when
    Jira did not send one, so the retry policy can honor it.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class MissingJiraCredentialsError(NetraJiraError):
    """No per-user Jira credentials could be resolved for this call (401-style).

    Never caught and replaced with a shared identity - same rule as
    MissingCredentialsError on the Confluence side.
    """
