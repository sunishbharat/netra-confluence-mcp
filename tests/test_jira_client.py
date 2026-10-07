from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from pytest_httpx import HTTPXMock

from exceptions import (
    JiraAPIError,
    JiraIssueNotFoundError,
    JiraNetworkError,
    JiraPermissionError,
    JiraRateLimitedError,
    MissingJiraCredentialsError,
)
from netra_jira.client import JiraClient, describe_error_body

BASE = "https://test.atlassian.net"
URL = f"{BASE}/rest/api/3/test"


@pytest.fixture
def sleep() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def jira(sleep: AsyncMock) -> JiraClient:
    client = JiraClient(base_url=BASE, site_url=BASE, email="a@example.com", token="t")
    client._sleep = sleep  # no real waiting between retries
    return client


def test_missing_credentials_raise() -> None:
    with pytest.raises(MissingJiraCredentialsError):
        JiraClient(base_url=BASE, site_url=BASE, email="", token="t")


def test_browse_url(jira: JiraClient) -> None:
    assert jira.browse_url("PROJ-1") == f"{BASE}/browse/PROJ-1"


async def test_sends_basic_auth(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(url=URL, json={})
    await jira.get("/rest/api/3/test")
    assert httpx_mock.get_requests()[0].headers["authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    ("status", "exc"),
    [(403, JiraPermissionError), (404, JiraIssueNotFoundError), (500, JiraAPIError)],
)
async def test_status_mapping(
    httpx_mock: HTTPXMock, jira: JiraClient, status: int, exc: type[Exception]
) -> None:
    httpx_mock.add_response(url=URL, status_code=status, json={"errorMessages": ["nope"]})
    with pytest.raises(exc, match="nope"):
        await jira.get("/rest/api/3/test")


async def test_429_retried_honoring_retry_after(
    httpx_mock: HTTPXMock, jira: JiraClient, sleep: AsyncMock
) -> None:
    httpx_mock.add_response(url=URL, status_code=429, headers={"Retry-After": "7"})
    httpx_mock.add_response(url=URL, json={"ok": True})
    response = await jira.get("/rest/api/3/test")
    assert response.json() == {"ok": True}
    sleep.assert_awaited_once_with(7.0)


async def test_retry_after_is_capped(
    httpx_mock: HTTPXMock, jira: JiraClient, sleep: AsyncMock
) -> None:
    httpx_mock.add_response(url=URL, status_code=429, headers={"Retry-After": "3600"})
    httpx_mock.add_response(url=URL, json={})
    await jira.get("/rest/api/3/test")
    sleep.assert_awaited_once_with(60.0)


async def test_429_retried_on_post(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    """A 429 is rejected before processing, so even a write is safe to retry."""
    httpx_mock.add_response(url=URL, method="POST", status_code=429)
    httpx_mock.add_response(url=URL, method="POST", status_code=201, json={})
    response = await jira.post("/rest/api/3/test", json={})
    assert response.status_code == 201


async def test_429_exhausts_after_three_attempts(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    for _ in range(3):
        httpx_mock.add_response(url=URL, status_code=429)
    with pytest.raises(JiraRateLimitedError):
        await jira.get("/rest/api/3/test")
    assert len(httpx_mock.get_requests()) == 3


async def test_get_retries_network_error(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"), url=URL)
    httpx_mock.add_response(url=URL, json={})
    await jira.get("/rest/api/3/test")
    assert len(httpx_mock.get_requests()) == 2


async def test_post_never_retries_network_error(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    """A lost POST response may have created issues - it must surface, not be re-sent."""
    httpx_mock.add_exception(httpx.ReadTimeout("lost"), url=URL, method="POST")
    with pytest.raises(JiraNetworkError):
        await jira.post("/rest/api/3/test", json={})
    assert len(httpx_mock.get_requests()) == 1


async def test_put_never_retries_network_error(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("lost"), url=URL, method="PUT")
    with pytest.raises(JiraNetworkError):
        await jira.put("/rest/api/3/test", json={})
    assert len(httpx_mock.get_requests()) == 1


async def test_idempotent_post_retries_network_error(
    httpx_mock: HTTPXMock, jira: JiraClient
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"), url=URL, method="POST")
    httpx_mock.add_response(url=URL, method="POST", json={})
    await jira.post("/rest/api/3/test", idempotent=True, json={})
    assert len(httpx_mock.get_requests()) == 2


async def test_allow_status_returns_response(httpx_mock: HTTPXMock, jira: JiraClient) -> None:
    httpx_mock.add_response(url=URL, method="POST", status_code=400, json={"errors": []})
    response = await jira.post("/rest/api/3/test", allow_status=frozenset({400}), json={})
    assert response.status_code == 400


def test_describe_error_body() -> None:
    body = {"errorMessages": ["bad"], "errors": {"summary": "too long"}}
    assert describe_error_body(body) == "bad; summary: too long"
    assert describe_error_body("plain") == "plain"
