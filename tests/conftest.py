from __future__ import annotations

import pytest

from confluence.client import ConfluenceClient
from models.config import NetraSettings


@pytest.fixture
def settings() -> NetraSettings:
    return NetraSettings(
        confluence_base_url="https://test.atlassian.net",
        confluence_api_token="test-token",
        confluence_user_email="test@example.com",
        confluence_site_url="https://test.atlassian.net",
    )


@pytest.fixture
def client(settings: NetraSettings) -> ConfluenceClient:
    return ConfluenceClient(settings)
