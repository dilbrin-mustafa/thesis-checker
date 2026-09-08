"""DOCX parser tests (Phase 1, week 4).

The point of these tests is not "does it parse" — it is that the *formatting
metadata* Phase 2 will judge the thesis against is resolved correctly through
the full style inheritance chain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.docx_parser import DocxParser, StyleTable
from app.ingestion.errors import ParseError
from app.models.ir import DocumentIR


@pytest.fixture()
def docx_ir(parsed: dict[str, DocumentIR]) -> DocumentIR:
    return parsed["docx"]


@pytest.fixture()
def manual_ir(parsed: dict[str, DocumentIR]) -> DocumentIR:
    return parsed["docx_manual"]


def test_source_metadata(docx_ir: DocumentIR) -> None:
    assert docx_ir.source.format == "docx"
    assert len(docx_ir.source.sha256) == 64
    assert docx_ir.source.words > 100
    assert docx_ir.source.pages >= 1  # from docProps/app.xml


def test_body_formatting_comes_from_the_default_style(docx_ir: DocumentIR) -> None:
    """A paragraph with no ``w:pStyle`` must still inherit Normal's formatting."""
    body = next(b for b in docx_ir.blocks if b.text.startswith("Praca przedstawia"))
    assert body.style.font == "Times New Roman"
    assert body.style.size_pt == 12.0
    assert body.style.line_spacing == 1.5
    assert body.style.indent_cm == 1.25
    assert body.style.align == "justify"


def test_theme_fonts_are_resolved(docx_ir: DocumentIR) -> None:
    """``asciiTheme="minorHAnsi"`` must resolve to a real font name."""
    heading = next(b for b in docx_ir.blocks if b.text == "Wstęp")
    assert heading.style.font is not None
    assert heading.style.size_pt is not None and heading.style.size_pt > 12.0


def test_headings_carry_levels_from_style_names(docx_ir: DocumentIR) -> None:
    levels = {b.text: b.level for b in docx_ir.blocks if b.type == "heading"}
    assert levels["Wstęp"] == 1
    assert levels["Metodyka badań"] == 1
    title = next(b for b in docx_ir.blocks if b.text == "POLITECHNIKA GDAŃSKA")
    assert title.type == "heading" and "title" in title.flags


def test_block_types(docx_ir: DocumentIR) -> None:
    by_type = {b.type for b in docx_ir.blocks}
    assert {
        "heading",
        "paragraph",
        "caption",
        "table",
        "code",
        "quote",
        "list_item",
        "bibitem",
    } <= by_type


def test_captions_and_floats(docx_ir: DocumentIR) -> None:
    assert len(docx_ir.figures) == 1
    assert docx_ir.figures[0].number == "1.1"
    assert len(docx_ir.tables) == 1
    assert docx_ir.tables[0].number == "2.1"
    # both are referenced in the body text → Phase 2 needs this link
    assert docx_ir.figures[0].referenced_by
    assert docx_ir.tables[0].referenced_by


def test_table_text_is_cell_separated(docx_ir: DocumentIR) -> None:
    table = next(b for b in docx_ir.blocks if b.type == "table")
    assert "Format\tF1" in table.text
    assert "DOCX\t0,93" in table.text


def test_page_geometry(docx_ir: DocumentIR) -> None:
    assert docx_ir.layout.page_size in ("A4", "Letter")
    assert docx_ir.layout.margins_cm["top"] > 0


def test_manual_headings_are_detected(manual_ir: DocumentIR) -> None:
    """No Heading styles at all: only bold + a larger font."""
    headings = [b for b in manual_ir.blocks if b.type == "heading"]
    assert [h.text for h in headings] == ["Wstęp", "Metodyka badań", "Bibliografia"]
    assert all("heading_inferred" in h.flags for h in headings)
    assert all(h.level == 1 for h in headings)


def test_citations_are_linked_to_the_bibliography(docx_ir: DocumentIR) -> None:
    first = next(c for c in docx_ir.citations if c.raw == "[1]")
    assert first.ref_id == docx_ir.references[0].id
    assert next(c for c in docx_ir.citations if c.raw == "[2, 3]").ref_id


def test_dangling_citations_stay_unlinked(docx_ir: DocumentIR) -> None:
    """The fixture cites [4-6] but lists only three references.

    That must *not* be papered over: an unresolved ``ref_id`` is exactly the
    orphan-citation signal Phase 3 reports.
    """
    assert len(docx_ir.references) == 3
    dangling = [c for c in docx_ir.citations if c.ref_id is None]
    assert dangling, "expected the out-of-range citation to stay unlinked"
    assert any(c.raw == "[4-6]" for c in dangling)


def test_references_are_parsed_from_the_bibliography(docx_ir: DocumentIR) -> None:
    assert len(docx_ir.references) == 3
    with_doi = [r for r in docx_ir.references if r.parsed.doi]
    assert with_doi and with_doi[0].parsed.doi == "10.1000/xyz123"
    assert all(r.parsed.year for r in docx_ir.references)
    assert all(0.0 < r.confidence <= 1.0 for r in docx_ir.references)


def test_footnotes_are_separate_blocks(tmp_path: Path) -> None:
    import docx

    document = docx.Document()
    document.add_heading("Wstęp", level=1)
    document.add_paragraph("Akapit z przypisem w treści pracy dyplomowej.")
    # python-docx has no footnote API; inject the part directly.
    path = tmp_path / "with_footnotes.docx"
    document.save(str(path))
    _inject_footnote(path)

    ir = DocxParser().parse(path, "doc-footnote", "with_footnotes.docx")
    footnotes = [b for b in ir.blocks if b.type == "footnote"]
    assert len(footnotes) == 1
    assert "przypisu" in footnotes[0].text
    assert "footnote" in (footnotes[0].locator.xml_path or "")


def _inject_footnote(path: Path) -> None:
    """Add a real ``word/footnotes.xml`` part plus a reference to it."""
    import shutil
    import zipfile

    staged = path.with_suffix(".tmp.docx")
    footnotes_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:id="1"><w:p><w:r><w:t>'
        "To jest treść przypisu dolnego."
        "</w:t></w:r></w:p></w:footnote>"
        "</w:footnotes>"
    )
    with (
        zipfile.ZipFile(path) as source,
        zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(
                    b"</Types>",
                    b'<Override PartName="/word/footnotes.xml" ContentType="application/vnd.'
                    b'openxmlformats-officedocument.wordprocessingml.footnotes+xml"/></Types>',
                )
            target.writestr(item, data)
        target.writestr("word/footnotes.xml", footnotes_xml)
    shutil.move(staged, path)


def test_broken_document_raises_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.docx"
    path.write_bytes(b"PK\x03\x04 this is not really a docx container")
    with pytest.raises(ParseError):
        DocxParser().parse(path, "doc-broken", "broken.docx")


def test_style_table_resolves_inheritance() -> None:
    """basedOn chains must be walked, not just the leaf style."""
    styles_xml = (
        b'<?xml version="1.0"?>'
        b'<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:docDefaults><w:rPrDefault><w:rPr>"
        b'<w:rFonts w:ascii="Calibri"/><w:sz w:val="22"/>'
        b"</w:rPr></w:rPrDefault></w:docDefaults>"
        b'<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        b'<w:name w:val="Normal"/><w:pPr><w:spacing w:line="360" w:lineRule="auto"/></w:pPr>'
        b"</w:style>"
        b'<w:style w:type="paragraph" w:styleId="Body">'
        b'<w:name w:val="Body Text"/><w:basedOn w:val="Normal"/>'
        b'<w:rPr><w:rFonts w:ascii="Times New Roman"/><w:sz w:val="24"/></w:rPr>'
        b"</w:style>"
        b"</w:styles>"
    )
    table = StyleTable(styles_xml)
    resolved = table.resolve("Body")
    assert resolved["font"] == "Times New Roman"  # overridden by the leaf
    assert resolved["size_pt"] == 12.0  # overridden by the leaf
    assert resolved["line_spacing"] == 1.5  # inherited from Normal
    assert table.resolve(None)["font"] == "Calibri"  # default style → docDefaults
