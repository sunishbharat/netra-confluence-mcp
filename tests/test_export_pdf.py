from __future__ import annotations

from pathlib import Path

import pytest

from confluence.export.pdf import _load_base_css, _wrap_html, no_network_url_fetcher, render_pdf

_FIXTURE = Path(__file__).parent / "fixtures" / "export_view_golden.html"


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except Exception:  # noqa: BLE001 - any import-time failure (missing system libs) means skip
        return False
    return True


requires_weasyprint = pytest.mark.skipif(
    not _weasyprint_available(),
    reason="WeasyPrint's system libs (Pango/Cairo/GDK-Pixbuf/HarfBuzz) are not installed on "
    "this machine; the render path is exercised in CI/Docker where they are baked in",
)


def test_no_network_url_fetcher_raises_for_any_url() -> None:
    with pytest.raises(ValueError, match="network fetch blocked"):
        no_network_url_fetcher("https://example.com/anything.png")


def test_no_network_url_fetcher_raises_for_data_uri_too() -> None:
    # Even a data: URI is rejected - after inlining nothing should call the
    # fetcher at all, so this is a bug-detector, not a legitimate path.
    with pytest.raises(ValueError, match="network fetch blocked"):
        no_network_url_fetcher("data:image/png;base64,AA==")


def test_wrap_html_inserts_markers_after_body_open_tag() -> None:
    html = "<html><body><h1>Title</h1></body></html>"
    wrapped = _wrap_html(html, title="My Title", timestamp="2026-07-02T00:00:00Z")
    assert wrapped.index("<body") < wrapped.index("netra-export-marker")
    assert wrapped.index("netra-export-marker") < wrapped.index("<h1>Title</h1>")
    assert "My Title" in wrapped
    assert "2026-07-02T00:00:00Z" in wrapped


def test_wrap_html_escapes_title() -> None:
    wrapped = _wrap_html("<body></body>", title="<script>x</script>", timestamp="t")
    assert "<script>x</script>" not in wrapped
    assert "&lt;script&gt;" in wrapped


def test_wrap_html_handles_missing_body_tag() -> None:
    wrapped = _wrap_html("<p>no body wrapper</p>", title="T", timestamp="ts")
    assert "netra-export-marker" in wrapped
    assert "<p>no body wrapper</p>" in wrapped


def test_base_css_has_expected_rules() -> None:
    css = _load_base_css()
    assert "@page" in css
    assert "string-set: netra-title" in css
    assert "string-set: netra-timestamp" in css
    assert "table-layout: fixed" in css
    assert "white-space: pre-wrap" in css
    assert "max-width: 100%" in css


@requires_weasyprint
def test_golden_fixture_renders_and_has_multiple_pages() -> None:
    import io

    from pypdf import PdfReader

    html = _FIXTURE.read_text(encoding="utf-8")
    pdf_bytes = render_pdf(
        html,
        title="R1.0 Release Report",
        timestamp="2026-07-02T00:00:00Z",
        page_size="A4",
    )
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) > 1


@requires_weasyprint
def test_golden_fixture_contains_title_and_footer_text() -> None:
    import io

    from pypdf import PdfReader

    html = _FIXTURE.read_text(encoding="utf-8")
    pdf_bytes = render_pdf(
        html,
        title="R1.0 Release Report",
        timestamp="2026-07-02T00:00:00Z",
        page_size="A4",
    )
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = "\n".join(page.extract_text() for page in reader.pages)
    assert "R1.0 Release Report" in full_text
    assert "exported" in full_text
    assert "2026-07-02T00:00:00Z" in full_text
    assert "page 1 of" in full_text.lower()


@requires_weasyprint
def test_golden_fixture_cjk_and_emoji_are_not_tofu() -> None:
    import io

    from pypdf import PdfReader

    html = _FIXTURE.read_text(encoding="utf-8")
    pdf_bytes = render_pdf(
        html,
        title="R1.0 Release Report",
        timestamp="2026-07-02T00:00:00Z",
        page_size="A4",
    )
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = "\n".join(page.extract_text() for page in reader.pages)
    assert "重要释放报告" in full_text


@requires_weasyprint
def test_letter_page_size_is_honored() -> None:
    html = "<html><body><p>hi</p></body></html>"
    a4 = render_pdf(html, title="T", timestamp="ts", page_size="A4")
    letter = render_pdf(html, title="T", timestamp="ts", page_size="LETTER")
    assert a4 != letter
