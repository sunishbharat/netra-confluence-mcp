from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from exceptions import (
    ConfluenceAPIError,
    ConfluencePermissionError,
    PageNotFoundError,
    VersionConflictError,
)
from models.config import NetraSettings


class ConfluenceClient:
    def __init__(self, settings: NetraSettings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.confluence_base_url,
            auth=httpx.BasicAuth(
                settings.confluence_user_email,
                settings.confluence_api_token,
            ),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            # httpx's 5s default is too tight for large ADF payloads (the R1.0
            # report alone is 43 Jira macros); connect timeout stays short so a
            # dead host still fails fast.
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    @property
    def site_url(self) -> str:
        return self._settings.confluence_site_url

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
        response = await self._request("GET", path, **kwargs)
        self._raise_for_status(response)
        return response

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
        response = await self._request("PUT", path, **kwargs)
        self._raise_for_status(response)
        return response

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
        response = await self._request("POST", path, **kwargs)
        self._raise_for_status(response)
        return response

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise ConfluenceAPIError(
                f"Network error calling Confluence ({method} {path}): {e}"
            ) from e

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> ConfluenceClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 403:
            raise ConfluencePermissionError(f"Permission denied (HTTP 403): {response.url}")
        if response.status_code == 404:
            raise PageNotFoundError(f"Page not found (HTTP 404): {response.url}")
        if response.status_code == 409:
            raise VersionConflictError(f"Version conflict (HTTP 409): {response.url}")
        if response.is_error:
            raise ConfluenceAPIError(
                f"Confluence API error HTTP {response.status_code}: {response.text}"
            )
