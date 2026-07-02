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
