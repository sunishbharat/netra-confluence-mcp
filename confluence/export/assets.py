from __future__ import annotations

import base64
import re
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from selectolax.parser import HTMLParser, Node

from confluence.client import ConfluenceClient
from exceptions import ConfluencePermissionError, NetraConfluenceError, PageNotFoundError
from models.config import NetraSettings
from models.export import AssetReport

log = structlog.get_logger()

_MEDIA_HOST_SUFFIX = ".media.atlassian.com"
_FALLBACK_MIME = "application/octet-stream"
_STYLE_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
_BLANK_PIXEL_DATA_URI = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="


def _placeholder_node(message: str) -> Node:
    fragment = HTMLParser(f'<span class="netra-export-placeholder">[{message}]</span>')
    body = fragment.body
    assert body is not None
    child = body.child
    assert child is not None
    return child


class _AssetLocalizer:
    """Stateful walk over one document's assets: caps, classification, fetch, inlining.

    All the per-document state (byte/asset budgets, the running AssetReport
    lists) is shared across every asset on the page, which is why this is a
    small stateful class rather than a pile of parameters threaded through
    free functions.
    """

    def __init__(
        self,
        *,
        client: ConfluenceClient,
        anon_client: httpx.AsyncClient,
        settings: NetraSettings,
    ) -> None:
        self._client = client
        self._anon_client = anon_client
        self._settings = settings
        self._site_host = (urlparse(settings.confluence_site_url).hostname or "").lower()
        self._total_bytes = 0
        self._asset_count = 0
        self.fetched: list[str] = []
        self.skipped_external: list[str] = []
        self.failed: list[str] = []

    def _classify(self, url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if not host or host == self._site_host:
            return "same_site"
        if host.endswith(_MEDIA_HOST_SUFFIX):
            return "media"
        return "external"

    async def _fetch(self, url: str, kind: str) -> tuple[bytes | None, str, str | None]:
        try:
            if kind == "same_site":
                parsed = urlparse(url)
                path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                response = await self._client.get(path)
            else:
                response = await self._anon_client.get(url)
        except ConfluencePermissionError:
            return None, _FALLBACK_MIME, "permission denied"
        except PageNotFoundError:
            return None, _FALLBACK_MIME, "not found"
        except (NetraConfluenceError, httpx.HTTPError) as e:
            return None, _FALLBACK_MIME, f"fetch error ({e.__class__.__name__})"

        if response.status_code >= 400:
            return None, _FALLBACK_MIME, f"http {response.status_code}"
        content_type = response.headers.get("content-type", _FALLBACK_MIME).split(";")[0].strip()
        return response.content, content_type or _FALLBACK_MIME, None

    async def resolve(self, raw_url: str) -> tuple[str | None, str | None]:
        """Resolve one asset URL to a data: URI, or (None, reason) on any failure/skip.

        Never raises - a failed or capped asset degrades to a placeholder by
        design, so the caller always gets a usable outcome.
        """
        self._asset_count += 1
        if self._asset_count > self._settings.export_max_assets:
            reason = "asset count cap exceeded"
            self.failed.append(f"{reason}: {raw_url}")
            return None, reason

        resolved = urljoin(self._settings.confluence_base_url, raw_url)
        kind = self._classify(resolved)

        if kind == "external":
            host = urlparse(resolved).hostname or resolved
            self.skipped_external.append(resolved)
            return None, f"external image omitted: {host}"

        if self._total_bytes >= self._settings.export_max_total_asset_bytes:
            reason = "total asset budget exceeded"
            self.failed.append(f"{reason}: {resolved}")
            return None, reason

        content, mime, error = await self._fetch(resolved, kind)
        if error is not None:
            self.failed.append(f"{error}: {resolved}")
            return None, error
        assert content is not None

        if len(content) > self._settings.export_max_asset_bytes:
            reason = "asset too large"
            self.failed.append(f"{reason}: {resolved}")
            return None, reason

        self._total_bytes += len(content)
        self.fetched.append(resolved)
        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{mime};base64,{b64}", None

    def report(self) -> AssetReport:
        return AssetReport(
            fetched=self.fetched,
            skipped_external=self.skipped_external,
            failed=self.failed,
            downscaled=[],
        )


async def localize_assets(
    html: str, *, client: ConfluenceClient, settings: NetraSettings
) -> tuple[str, AssetReport]:
    """Inline same-site and Atlassian media-CDN assets as data: URIs; placeholder
    everything else; strip <script>. A single asset failure never fails the
    export - it always degrades to a placeholder and an AssetReport entry.
    """
    tree = HTMLParser(html)

    for script in tree.css("script"):
        script.decompose()

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0)) as anon_client:
        localizer = _AssetLocalizer(client=client, anon_client=anon_client, settings=settings)

        for img in tree.css("img[src]"):
            src = img.attrs.get("src")
            if not src:
                continue
            data_uri, reason = await localizer.resolve(src)
            if data_uri is not None:
                img.attrs["src"] = data_uri
            else:
                # selectolax's stub types replace_with as str | bytes | None only,
                # but it accepts (and documents) a Node at runtime to insert real
                # HTML rather than escaped text - see _placeholder_node.
                img.replace_with(_placeholder_node(reason or "image omitted"))  # type: ignore[arg-type]

        for image in tree.css("image"):
            href = image.attrs.get("href") or image.attrs.get("xlink:href")
            if not href:
                continue
            data_uri, _reason = await localizer.resolve(href)
            replacement = data_uri or _BLANK_PIXEL_DATA_URI
            if "href" in image.attrs:
                image.attrs["href"] = replacement
            if "xlink:href" in image.attrs:
                image.attrs["xlink:href"] = replacement

        for styled in tree.css("[style]"):
            style = styled.attrs.get("style")
            if not style or "url(" not in style:
                continue
            new_style = await _rewrite_style_urls(style, localizer)
            if new_style != style:
                styled.attrs["style"] = new_style

    return tree.html or "", localizer.report()


async def _rewrite_style_urls(style: str, localizer: _AssetLocalizer) -> str:
    matches = list(_STYLE_URL_RE.finditer(style))
    if not matches:
        return style

    result: list[str] = []
    last_end = 0
    for match in matches:
        result.append(style[last_end : match.start()])
        raw_url = match.group(1)
        data_uri, _reason = await localizer.resolve(raw_url)
        replacement = data_uri or _BLANK_PIXEL_DATA_URI
        result.append(f'url("{replacement}")')
        last_end = match.end()
    result.append(style[last_end:])
    return "".join(result)
