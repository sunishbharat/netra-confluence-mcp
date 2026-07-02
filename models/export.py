from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ExportErrorCode = Literal[
    "INVALID_URL",
    "WRONG_SITE",
    "PAGE_NOT_FOUND",
    "PERMISSION_DENIED",
    "PAGE_TOO_LARGE",
    "EXPORT_TIMEOUT",
    "TOO_LARGE_FOR_INLINE",
    "STORAGE_FAILED",
    "RATE_LIMITED",
    "API_ERROR",
]


class ExportPdfRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page_url: str = Field(
        ..., description="Any accepted Confluence page URL shape, or a bare page id"
    )
    delivery: Literal["link", "inline"] = Field(
        default="link",
        description="'link' returns a time-limited download URL; 'inline' returns base64 PDF bytes",
    )
    page_size: Literal["A4", "LETTER"] = Field(default="A4", description="@page size for the PDF")
    filename: str | None = Field(
        default=None, description="Download filename; defaults to slugified title + date"
    )


class AssetReport(BaseModel):
    """Fidelity-gap disclosure for images localized (or not) during export.

    Each list holds one human-readable entry per asset - not just a count -
    so the tool response can name exactly which assets degraded and why.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fetched: list[str] = Field(
        default_factory=list, description="Same-site or media-CDN asset URLs successfully inlined"
    )
    skipped_external: list[str] = Field(
        default_factory=list,
        description="Asset URLs on non-allowlisted hosts, replaced with a placeholder",
    )
    failed: list[str] = Field(
        default_factory=list,
        description=(
            "Asset fetches that errored (403/404/timeout/over-cap), replaced with a placeholder"
        ),
    )
    downscaled: list[str] = Field(
        default_factory=list,
        description="Reserved for future Pillow downscaling; always empty in this phase",
    )


class ExportPdfResult(BaseModel):
    """Response contract for export_page_pdf (addendum section 3.5).

    Adds `pdf_base64` beyond the documented contract: the addendum describes
    inline delivery returning base64 PDF bytes but the listed model has no
    field for the payload itself (`pdf_bytes` is a size in bytes, not
    content, matched by its `int | None` type alongside `pdf_sha256`). This
    field is the necessary carrier for that payload; it is None for every
    status other than a successful inline delivery.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["OK", "ERROR"]
    error_code: ExportErrorCode | None = None
    page_id: str | None = None
    page_title: str | None = None
    page_version: int | None = Field(
        default=None, description="Version that was rendered - export provenance"
    )
    pdf_sha256: str | None = None
    pdf_bytes: int | None = Field(default=None, description="Rendered PDF size in bytes")
    delivery: Literal["link", "inline"] | None = None
    download_url: str | None = Field(default=None, description="link mode only")
    expires_at: datetime | None = Field(
        default=None, description="link mode only - UTC expiry of the download URL"
    )
    pdf_base64: str | None = Field(
        default=None, description="inline mode only - base64-encoded PDF bytes"
    )
    asset_report: AssetReport | None = None
    message: str = Field(
        ...,
        description="Always repeats download_url + expiry (or the error) in prose so LLM "
        "callers surface it verbatim to the user",
    )
