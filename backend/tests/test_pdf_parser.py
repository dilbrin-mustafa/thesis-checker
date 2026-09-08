"""PDF parser tests (Phase 1, week 6 — best effort, but measured).

The headline behaviour under test: when the document carries a ToC, the ToC
decides heading levels; when it does not, font-size clustering takes over and
says so in ``extra["pdf"]["heading_source"]``.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.core.config import Settings
from app.ingestion.errors import EncryptedDocumentError, ResourceLimitError
from app.ingestion.pdf_parser import PdfParser, _expand_ligatures, _join_lines
from app.models.ir import DocumentIR


@pytest.fixture()
def pdf_ir(parsed: dict[str, DocumentIR]) -> DocumentIR:
    return parsed["pdf"]


@pytest.fixture()
def no_toc_ir(parsed: dict[str, DocumentIR]) -> DocumentIR:
    return parsed["pdf_no_toc"]


def test_page_count_and_geometry(pdf_ir: DocumentIR) -> None:
    assert pdf_ir.source.pages == 3
    assert pdf_ir.layout.page_size == "A4"
    assert pdf_ir.layout.margins_cm["inner"] > 0


def test_toc_validates_heading_levels(pdf_ir: DocumentIR) -> None:
    assert pdf_ir.extra["pdf"]["toc_entries"] == 3
    assert pdf_ir.extra["pdf"]["heading_source"] == "toc+font-size"
    levels = {b.text: b.level for b in pdf_ir.blocks if b.type == "heading"}
    # "Spis treści" and "Bibliografia" are *not* in the ToC: they inherit the
    # corrected size→level mapping from the ToC-matched headings.
    assert levels["Streszczenie"] == 1
    assert levels["Spis treści"] == 1
    assert levels["Bibliografia"] == 1


def test_headings_without_a_toc_fall_back_to_font_size(no_toc_ir: DocumentIR) -> None:
    assert no_toc_ir.extra["pdf"]["heading_source"] == "font-size clustering"
    headings = [b for b in no_toc_ir.blocks if b.type == "heading"]
    assert [h.text for h in headings] == ["Wstęp", "Bibliografia"]
    assert all("heading_inferred" in h.flags for h in headings)


def test_every_block_carries_page_and_bbox(pdf_ir: DocumentIR) -> None:
    for block in pdf_ir.blocks:
        assert block.locator.page is not None
        assert block.locator.bbox is not None and len(block.locator.bbox) == 4


def test_captions_floats_and_tables(pdf_ir: DocumentIR) -> None:
    assert [f.number for f in pdf_ir.figures] == ["1.1"]
    assert [t.number for t in pdf_ir.tables] == ["2.1"]
    table_block = next(b for b in pdf_ir.blocks if b.type == "table")
    assert "table_detected" in table_block.flags
    assert "DOCX" in table_block.text


def test_text_inside_a_detected_table_is_not_duplicated(pdf_ir: DocumentIR) -> None:
    """Table text must appear once — as the table block, not also as prose."""
    occurrences = sum(1 for b in pdf_ir.blocks if "0,93" in b.text)
    assert occurrences == 1


def test_ligatures_are_expanded() -> None:
    assert _expand_ligatures("bibliogra\ufb01a") == "bibliografia"


def test_hyphenated_line_breaks_are_joined() -> None:
    lines = [
        {"spans": [{"text": "To jest wyraz anal-", "font": "T", "size": 12.0, "flags": 0}]},
        {"spans": [{"text": "ityczny w zdaniu.", "font": "T", "size": 12.0, "flags": 0}]},
    ]
    assert _join_lines(lines) == "To jest wyraz analityczny w zdaniu."


def test_bold_and_size_are_recovered(pdf_ir: DocumentIR) -> None:
    heading = next(b for b in pdf_ir.blocks if b.text == "1. Wstęp")
    assert heading.style.bold is True
    body = next(b for b in pdf_ir.blocks if b.text.startswith("Niniejsza praca"))
    assert body.style.size_pt == 12.0
    assert body.style.bold in (False, None)


def test_bibliography_is_excluded(pdf_ir: DocumentIR) -> None:
    reasons = {e.reason for e in pdf_ir.exclusion_mask}
    assert "bibliography" in reasons
    bibitem = next(b for b in pdf_ir.blocks if b.type == "bibitem")
    assert "excluded_from_analysis" in bibitem.flags


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(
        str(path),
        # constant read via getattr: pymupdf's stubs do not export it
        encryption=int(getattr(pymupdf, "PDF_ENCRYPT_AES_256", 5)),
        user_pw="secret",
        owner_pw="secret",
    )
    doc.close()
    with pytest.raises(EncryptedDocumentError):
        PdfParser().parse(path, "doc-locked", "locked.pdf")


def test_page_cap_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "long.pdf"
    doc = pymupdf.open()
    for _ in range(4):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    settings = Settings(max_pages=2)
    with pytest.raises(ResourceLimitError):
        PdfParser(settings).parse(path, "doc-long", "long.pdf")


def test_corrupt_pdf_is_rejected(tmp_path: Path) -> None:
    """A file with a PDF header but no valid body must fail cleanly."""
    from app.ingestion.errors import ParseError

    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\n this is not a real pdf body\n")
    with pytest.raises(ParseError):
        PdfParser().parse(path, "doc-corrupt", "corrupt.pdf")
