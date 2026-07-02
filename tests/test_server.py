from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import server as module
from models.config import NetraSettings


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_SITE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "test-token")
    monkeypatch.setenv("CONFLUENCE_USER_EMAIL", "test@example.com")
    monkeypatch.setenv("SERVER_TRANSPORT", "stdio")


def _fake_request(token: str) -> SimpleNamespace:
    return SimpleNamespace(path_params={"token": token})


async def test_download_export_serves_stored_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_store = MagicMock()
    fake_store.get = AsyncMock(return_value=(b"%PDF-1.4 bytes", "report.pdf"))
    monkeypatch.setattr(module, "get_default_store", lambda max_bytes: fake_store)  # noqa: ARG005

    response = await module.download_export(_fake_request("tok123"))  # type: ignore[arg-type]

    assert response.status_code == 200
    assert response.body == b"%PDF-1.4 bytes"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="report.pdf"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_download_export_unknown_token_returns_410(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_store = MagicMock()
    fake_store.get = AsyncMock(return_value=None)
    monkeypatch.setattr(module, "get_default_store", lambda max_bytes: fake_store)  # noqa: ARG005

    response = await module.download_export(_fake_request("does-not-exist"))  # type: ignore[arg-type]

    assert response.status_code == 410
    assert b"expired" in response.body


def _settings(**overrides: object) -> NetraSettings:
    base: dict[str, object] = {
        "confluence_base_url": "https://test.atlassian.net",
        "confluence_site_url": "https://test.atlassian.net",
        "confluence_api_token": "test-token",
        "confluence_user_email": "test@example.com",
    }
    base.update(overrides)
    return NetraSettings(_env_file=None, **base)  # type: ignore[call-arg,arg-type]


def test_no_scale_out_warning_when_instance_index_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
    warn = MagicMock()
    monkeypatch.setattr(module.logger, "warning", warn)
    module._warn_if_unsafe_memory_export_store(_settings())
    warn.assert_not_called()


def test_no_scale_out_warning_when_instance_index_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CF_INSTANCE_INDEX", raising=False)
    warn = MagicMock()
    monkeypatch.setattr(module.logger, "warning", warn)
    module._warn_if_unsafe_memory_export_store(_settings())
    warn.assert_not_called()


def test_scale_out_warning_when_instance_index_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CF_INSTANCE_INDEX", "2")
    warn = MagicMock()
    monkeypatch.setattr(module.logger, "warning", warn)
    module._warn_if_unsafe_memory_export_store(_settings())
    warn.assert_called_once()


def test_no_scale_out_warning_when_store_is_not_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CF_INSTANCE_INDEX", "2")
    warn = MagicMock()
    monkeypatch.setattr(module.logger, "warning", warn)
    module._warn_if_unsafe_memory_export_store(_settings(export_store="s3"))
    warn.assert_not_called()
