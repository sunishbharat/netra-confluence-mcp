"""Phase 0 feasibility spike for the Playwright PDF export redesign.

Throwaway script - not part of confluence/, not committed to git, not part of
the test suite. See docs/netra-mcp-export-pdf-playwright-design.md section 3.

Answers one question: does Confluence Cloud serve the flyingpdf export URL to
a request carrying only an Authorization: Basic header (attached to every
request the Playwright BrowserContext makes), or does the HTML shell gate on
a session cookie regardless of that header?

Fill in PAGE_ID below with a real page id from your tenant, then run:

    uv run python scratch/spike_export.py

Reads CONFLUENCE_BASE_URL / CONFLUENCE_USER_EMAIL / CONFLUENCE_API_TOKEN from
.env via NetraSettings, same as the rest of this project - no credentials are
hardcoded here.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from models.config import NetraSettings

# --- fill this in before running -------------------------------------------
PAGE_ID = "REPLACE_WITH_REAL_PAGE_ID"
# -----------------------------------------------------------------------------

EXPORT_TIMEOUT_MS = 90_000
OUTPUT_PATH = Path(__file__).parent / "spike_export_output.pdf"


async def main() -> None:
    if PAGE_ID == "REPLACE_WITH_REAL_PAGE_ID":
        print("Set PAGE_ID to a real Confluence page id before running.", file=sys.stderr)
        raise SystemExit(1)

    settings = NetraSettings()  # type: ignore[call-arg]  # fields read from env
    if not settings.confluence_user_email or not settings.confluence_api_token:
        print(
            "CONFLUENCE_USER_EMAIL and CONFLUENCE_API_TOKEN must be set in .env.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    base_url = settings.confluence_base_url.rstrip("/")
    export_url = f"{base_url}/wiki/spaces/flyingpdf/pdfpageexport.action?pageId={PAGE_ID}"
    auth_header = base64.b64encode(
        f"{settings.confluence_user_email}:{settings.confluence_api_token}".encode()
    ).decode()

    print(f"Target: {export_url}")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True)
        await context.set_extra_http_headers({"Authorization": f"Basic {auth_header}"})
        page = await context.new_page()

        try:
            async with page.expect_download(timeout=EXPORT_TIMEOUT_MS) as download_info:
                await page.goto(export_url, wait_until="networkidle")
            download = await download_info.value
        except PlaywrightTimeoutError:
            final_url = page.url
            if "id.atlassian.com" in final_url:
                print("OUTCOME (b): redirected to Atlassian SSO login.")
                print(f"  Final URL: {final_url}")
                print(
                    "  Header-only Basic auth did not authenticate the HTML shell. "
                    "storage_state fallback (design doc section 4.3) is required."
                )
            else:
                print("OUTCOME (c): no download fired and no SSO redirect - likely 404/error page.")
                print(f"  Final URL: {final_url}")
                print(
                    "  This tenant probably does not expose the legacy flyingpdf action. "
                    "Fall back to Strategy B (UI-menu click-through, design doc section 4.2)."
                )
            await browser.close()
            return

        path = await download.path()
        if path is None:
            print("Download event fired but no file path was produced - unexpected, investigate manually.")
            await browser.close()
            return

        pdf_bytes = path.read_bytes()
        OUTPUT_PATH.write_bytes(pdf_bytes)
        print("OUTCOME (a): PDF downloaded successfully.")
        print(f"  Bytes: {len(pdf_bytes)}")
        print(f"  Saved to: {OUTPUT_PATH}")
        print("  Header-only Basic auth via set_extra_http_headers works. Ship Strategy A as-is.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
