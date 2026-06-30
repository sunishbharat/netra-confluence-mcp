from __future__ import annotations


class NetraConfluenceError(Exception):
    """Base exception for all Netra Confluence errors."""


class VersionConflictError(NetraConfluenceError):
    """Page was modified between read and write (HTTP 409)."""


class AdfValidationError(NetraConfluenceError):
    """ADF structure is invalid - write blocked."""


class ConfluencePermissionError(NetraConfluenceError):
    """Caller lacks the required Confluence permission (HTTP 403)."""


class PageNotFoundError(NetraConfluenceError):
    """Requested page does not exist (HTTP 404)."""


class ConfluenceAPIError(NetraConfluenceError):
    """Unclassified Confluence API error."""
