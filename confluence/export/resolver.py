from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from confluence.client import ConfluenceClient
from exceptions import InvalidUrlError, PageNotFoundError, WrongSiteError
from models.config import NetraSettings

ACCEPTED_SHAPES = (
    "a bare numeric page id, e.g. '4587521'",
    "a modern page URL, e.g. '.../wiki/spaces/ENG/pages/4587521/Release+Report'",
    "a legacy view action, e.g. '.../pages/viewpage.action?pageId=4587521'",
    "a tiny link, e.g. '.../wiki/x/AbCdE'",
    "a display URL, e.g. '.../wiki/display/ENG/Release+Report'",
)

_MAX_REDIRECTS = 3

_BARE_ID_RE = re.compile(r"^\d+$")
_MODERN_PATH_RE = re.compile(r"/pages/(\d+)(?:/|$)")
_TINY_LINK_RE = re.compile(r"^/wiki/x/[A-Za-z0-9_-]+$")
_DISPLAY_PATH_RE = re.compile(r"^/wiki/display/([^/]+)/([^/?#]+)")


def _invalid_url_error(raw: str) -> InvalidUrlError:
    shapes = "; ".join(ACCEPTED_SHAPES)
    return InvalidUrlError(f"Unrecognized page URL '{raw}'. Accepted shapes: {shapes}")


async def resolve_page_id(
    page_url: str,
    client: ConfluenceClient,
    settings: NetraSettings,
    *,
    _redirect_depth: int = 0,
) -> str:
    """Resolve any accepted URL shape (or a bare page id) to a Confluence page id.

    Hard invariant enforced first, before any network call: scheme must be
    https and host must exactly equal the host of
    NetraSettings.confluence_site_url (WRONG_SITE). This is the SSRF /
    wrong-tenant guard - the server must never fetch an arbitrary
    user-supplied URL.
    """
    raw = page_url.strip()
    if not raw:
        raise _invalid_url_error(page_url)

    if _BARE_ID_RE.match(raw):
        return raw

    parsed = urlparse(raw)
    site_host = (urlparse(settings.confluence_site_url).hostname or "").lower()

    if parsed.scheme != "https":
        raise WrongSiteError(f"URL scheme must be https, got '{parsed.scheme or '(none)'}': {raw}")
    if parsed.username or parsed.password:
        raise WrongSiteError(f"URLs with embedded userinfo are not accepted: {raw}")
    if (parsed.hostname or "").lower() != site_host:
        raise WrongSiteError(
            f"URL host '{parsed.hostname}' does not match configured site host '{site_host}'"
        )

    path = parsed.path

    modern_match = _MODERN_PATH_RE.search(path)
    if modern_match:
        return modern_match.group(1)

    if path.endswith("/viewpage.action"):
        page_ids = parse_qs(parsed.query).get("pageId", [])
        if page_ids and page_ids[0].isdigit():
            return page_ids[0]
        raise _invalid_url_error(raw)

    if _TINY_LINK_RE.match(path):
        return await _resolve_via_redirect(raw, client, settings, _redirect_depth)

    display_match = _DISPLAY_PATH_RE.match(path)
    if display_match:
        try:
            return await _resolve_via_redirect(raw, client, settings, _redirect_depth)
        except InvalidUrlError:
            space_key = unquote(display_match.group(1))
            title = unquote(display_match.group(2)).replace("+", " ")
            return await _resolve_via_cql(client, space_key, title)

    raise _invalid_url_error(raw)


async def _resolve_via_redirect(
    raw: str,
    client: ConfluenceClient,
    settings: NetraSettings,
    redirect_depth: int,
) -> str:
    if redirect_depth >= _MAX_REDIRECTS:
        raise _invalid_url_error(raw)

    parsed = urlparse(raw)
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    response = await client.get(target, follow_redirects=False)

    location = response.headers.get("location")
    if not location:
        raise _invalid_url_error(raw)

    next_url = urljoin(raw, location)
    return await resolve_page_id(next_url, client, settings, _redirect_depth=redirect_depth + 1)


def _escape_cql_string(value: str) -> str:
    """Escape a value for embedding inside a double-quoted CQL string literal.

    space_key/title come from the caller-supplied URL path, not a trusted
    source - unescaped quotes would let a crafted URL break out of the
    string literal and inject arbitrary CQL clauses.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _resolve_via_cql(client: ConfluenceClient, space_key: str, title: str) -> str:
    cql = f'space="{_escape_cql_string(space_key)}" and title="{_escape_cql_string(title)}"'
    response = await client.get("/wiki/rest/api/content/search", params={"cql": cql})
    data: dict[str, object] = response.json()
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise PageNotFoundError(
            f"No page found for space '{space_key}' title '{title}' (CQL fallback)"
        )
    first = results[0]
    if not isinstance(first, dict) or "id" not in first:
        raise PageNotFoundError(
            f"No page found for space '{space_key}' title '{title}' (CQL fallback)"
        )
    return str(first["id"])
