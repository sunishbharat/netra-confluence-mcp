from __future__ import annotations

import pytest

from confluence.export.store import InMemoryExportStore, get_default_store
from exceptions import StorageFailedError


async def test_put_then_get_round_trips() -> None:
    store = InMemoryExportStore(max_bytes=1_000)
    path = await store.put("tok1", b"pdf-bytes", "report.pdf", ttl_s=60)
    assert path == "/exports/tok1"

    result = await store.get("tok1")
    assert result == (b"pdf-bytes", "report.pdf")


async def test_get_unknown_token_returns_none() -> None:
    store = InMemoryExportStore(max_bytes=1_000)
    assert await store.get("does-not-exist") is None


async def test_expired_entry_is_swept_on_get(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryExportStore(max_bytes=1_000)
    now = 1_000_000.0
    monkeypatch.setattr("confluence.export.store.time.time", lambda: now)
    await store.put("tok1", b"data", "f.pdf", ttl_s=10)

    monkeypatch.setattr("confluence.export.store.time.time", lambda: now + 11)
    assert await store.get("tok1") is None
    assert len(store) == 0


async def test_expired_entry_is_swept_on_put(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryExportStore(max_bytes=1_000)
    now = 1_000_000.0
    monkeypatch.setattr("confluence.export.store.time.time", lambda: now)
    await store.put("tok1", b"data", "f.pdf", ttl_s=10)

    monkeypatch.setattr("confluence.export.store.time.time", lambda: now + 11)
    await store.put("tok2", b"data2", "g.pdf", ttl_s=10)
    assert len(store) == 1  # tok1 swept away, only tok2 remains


async def test_lru_eviction_under_byte_cap() -> None:
    store = InMemoryExportStore(max_bytes=10)
    await store.put("tok1", b"12345", "a.pdf", ttl_s=60)  # 5 bytes
    await store.put("tok2", b"12345", "b.pdf", ttl_s=60)  # 5 bytes, total 10 - fits
    await store.put("tok3", b"12345", "c.pdf", ttl_s=60)  # forces eviction of tok1

    assert await store.get("tok1") is None, "oldest entry must be evicted to make room"
    assert (await store.get("tok2")) is not None
    assert (await store.get("tok3")) is not None


async def test_single_pdf_larger_than_cap_raises_storage_failed() -> None:
    store = InMemoryExportStore(max_bytes=4)
    with pytest.raises(StorageFailedError):
        await store.put("tok1", b"12345", "a.pdf", ttl_s=60)


async def test_token_is_unguessable_shape() -> None:
    store = InMemoryExportStore(max_bytes=10_000)
    # secrets.token_urlsafe isn't produced by the store itself (the caller
    # supplies it per the addendum's put() signature) - this asserts the
    # store accepts and round-trips a realistically-shaped 256-bit token.
    import secrets

    token = secrets.token_urlsafe(32)
    assert len(token) >= 40
    await store.put(token, b"x", "f.pdf", ttl_s=60)
    assert await store.get(token) == (b"x", "f.pdf")


async def test_get_default_store_returns_same_instance() -> None:
    import confluence.export.store as module

    module._default_store = None
    try:
        first = get_default_store(max_bytes=100)
        second = get_default_store(max_bytes=999)  # ignored - already created
        assert first is second
    finally:
        module._default_store = None
