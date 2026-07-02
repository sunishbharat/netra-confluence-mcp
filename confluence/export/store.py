from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

from exceptions import StorageFailedError


class ExportStore(Protocol):
    async def put(self, token: str, pdf: bytes, filename: str, ttl_s: int) -> str:
        """Persist pdf under token, return a URL for it.

        NOTE (deviation): InMemoryExportStore returns a path relative to this
        server (e.g. "/exports/<token>"), not a fully-qualified URL - a
        bytes-in-memory store has no way to know its own externally-visible
        host (CF's router hostname differs from the container's bind
        host/port). The tool layer composes the absolute download_url from
        the live HTTP request's base_url, which does know it. A future S3
        backend returns a fully-qualified presigned URL here directly, since
        it doesn't depend on this app's request context at all.
        """
        ...


@dataclass
class _Entry:
    pdf: bytes
    filename: str
    expires_at: float


class InMemoryExportStore:
    """Self-served /exports/{token} backend (addendum section 4, Backend A).

    Valid only while the deployment is single-instance (no session affinity) -
    the bytes live in this one process's memory. server.py logs a startup
    warning if EXPORT_STORE=memory and the CF instance index indicates
    scale-out. Restart/redeploy drops pending links; acceptable for a
    30-minute TTL artifact - the user just re-runs the export.

    Eviction under `max_bytes` is insertion-order (oldest first), not
    access-time LRU: these tokens are single-purpose bearer downloads meant
    to be fetched once shortly after creation, so insertion order already
    approximates recency for this workload without the bookkeeping a real
    LRU would need.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._total_bytes = 0

    def _sweep_expired(self) -> None:
        now = time.time()
        expired = [token for token, entry in self._entries.items() if entry.expires_at <= now]
        for token in expired:
            self._total_bytes -= len(self._entries.pop(token).pdf)

    async def put(self, token: str, pdf: bytes, filename: str, ttl_s: int) -> str:
        self._sweep_expired()

        size = len(pdf)
        if size > self._max_bytes:
            raise StorageFailedError(
                f"PDF ({size} bytes) exceeds the export store's total capacity "
                f"({self._max_bytes} bytes)"
            )

        while self._entries and self._total_bytes + size > self._max_bytes:
            _, evicted = self._entries.popitem(last=False)
            self._total_bytes -= len(evicted.pdf)

        self._entries[token] = _Entry(pdf=pdf, filename=filename, expires_at=time.time() + ttl_s)
        self._total_bytes += size
        return f"/exports/{token}"

    async def get(self, token: str) -> tuple[bytes, str] | None:
        """Return (pdf, filename) for a live token, or None if unknown/expired.

        Sweeps expired entries first, so this also serves as the periodic
        cleanup the class docstring promises ("swept on access").
        """
        self._sweep_expired()
        entry = self._entries.get(token)
        if entry is None:
            return None
        return entry.pdf, entry.filename

    def __len__(self) -> int:
        return len(self._entries)


_default_store: InMemoryExportStore | None = None


def get_default_store(max_bytes: int) -> InMemoryExportStore:
    """Process-wide singleton so the tool's put() and the /exports/{token}
    route's get() operate on the same in-memory bytes.

    max_bytes is only applied the first time the singleton is created - the
    value is env-driven and stable for the life of the process, so later
    calls with a different value are a no-op rather than resizing the store.
    """
    global _default_store
    if _default_store is None:
        _default_store = InMemoryExportStore(max_bytes=max_bytes)
    return _default_store
