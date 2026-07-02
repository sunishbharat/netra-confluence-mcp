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


class RateLimitedError(NetraConfluenceError):
    """Confluence returned HTTP 429 (rate limited)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class InvalidUrlError(NetraConfluenceError):
    """A page_url does not match any accepted URL shape."""


class WrongSiteError(NetraConfluenceError):
    """A page_url's scheme or host does not match the configured Confluence site.

    Raised before any network call - this is the SSRF / wrong-tenant guard.
    """


class PageTooLargeError(NetraConfluenceError):
    """export_view HTML exceeds EXPORT_MAX_HTML_BYTES."""

    def __init__(self, measured: int, cap: int) -> None:
        self.measured = measured
        self.cap = cap
        super().__init__(f"export_view HTML is {measured} bytes, exceeds cap of {cap} bytes")


class ExportTimeoutError(NetraConfluenceError):
    """The export pipeline (fetch + assets + render) exceeded EXPORT_TIMEOUT_SECONDS."""


class TooLargeForInlineError(NetraConfluenceError):
    """Rendered PDF exceeds EXPORT_INLINE_MAX_BYTES and delivery='inline' was requested."""

    def __init__(self, measured: int, cap: int) -> None:
        self.measured = measured
        self.cap = cap
        super().__init__(f"PDF is {measured} bytes, exceeds inline cap of {cap} bytes")


class StorageFailedError(NetraConfluenceError):
    """The export store failed to persist the rendered PDF for link delivery."""
