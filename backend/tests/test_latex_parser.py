"""LaTeX parser tests (Phase 1, week 5).

Two properties matter most here, and both have a test: semantics come out of
the markup (so nothing is inferred), and **no TeX engine is ever invoked** —
the sandbox rules in doc 02 §3 depend on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.latex_parser import LatexParser
from app.models.ir import DocumentIR


@pytest.fixture()
def latex_ir(parsed: dict[str, DocumentIR]) -> DocumentIR:
    return parsed["latex"]


@pytest.fixture()
def article_ir(parsed: dict[str, DocumentIR]) -> DocumentIR:
    return parsed["latex_article"]


def test_document_class_and_packages_are_captured(latex_ir: DocumentIR) -> None:
    """Format rules for LaTeX assert on the class, not on fonts (doc 01 §4.3)."""
    latex = latex_ir.extra["latex"]
    assert latex["documentclass"] == "report"
    assert "a4paper" in latex["class_options"]
    assert "listings" in latex["packages"]
    assert latex["title"].startswith("Automatyczna kontrola")


def test_includes_are_resolved_inside_the_project(latex_ir: DocumentIR) -> None:
    units = latex_ir.extra["latex"]["units"]
    assert "main.tex" in units
    assert any(unit.endswith("metodyka.tex") for unit in units)
    assert any(b.text.startswith("Zastosowano metody") for b in latex_ir.blocks)


def test_chapter_numbering_maps_to_level_one(latex_ir: DocumentIR) -> None:
    levels = {b.text: b.level for b in latex_ir.blocks if b.type == "heading"}
    assert levels["Wstęp"] == 1
    assert levels["Podsumowanie"] == 1
    assert levels["Metodyka badań"] == 1


def test_article_class_sections_start_at_level_one(article_ir: DocumentIR) -> None:
    levels = {b.text: b.level for b in article_ir.blocks if b.type == "heading"}
    assert levels["Wstęp"] == 1
    assert levels["Wyniki"] == 1
    assert levels["Zakres pracy"] == 2


def test_cite_keys_become_citations_with_ref_ids(latex_ir: DocumentIR) -> None:
    assert all(c.style == "bibtex_key" for c in latex_ir.citations)
    assert {c.ref_id for c in latex_ir.citations} <= {r.id for r in latex_ir.references}
    assert any(c.ref_id == "smith2019" for c in latex_ir.citations)
    # the rendered span still highlights something the reader can see
    for citation in latex_ir.citations:
        assert latex_ir.plain_text[citation.span.start : citation.span.end].startswith("[")


def test_bib_file_is_parsed_structurally(article_ir: DocumentIR) -> None:
    by_id = {r.id: r for r in article_ir.references}
    assert set(by_id) == {"kowalski2020", "smith2019"}
    assert by_id["smith2019"].parsed.doi == "10.1000/xyz123"
    assert by_id["smith2019"].parsed.year == 2019
    assert by_id["smith2019"].parsed.authors == ["John Smith", "Alice Jones"]
    assert by_id["kowalski2020"].parsed.title == "Metody analizy tekstu naukowego"
    assert by_id["kowalski2020"].confidence > 0.7


def test_thebibliography_becomes_bibitem_blocks(latex_ir: DocumentIR) -> None:
    bibitems = [b for b in latex_ir.blocks if b.type == "bibitem"]
    assert len(bibitems) == 3
    assert any("bibkey:kowalski2020" in b.flags for b in bibitems)


def test_environments_map_to_block_types(latex_ir: DocumentIR) -> None:
    kinds = {b.type for b in latex_ir.blocks}
    assert {"caption", "table", "equation", "list_item", "code", "quote", "footnote"} <= kinds
    code = next(b for b in latex_ir.blocks if b.type == "code")
    assert "return 0" in code.text
    assert "env:lstlisting" in code.flags
    equation = next(b for b in latex_ir.blocks if b.type == "equation")
    assert "mc^2" in equation.text


def test_float_labels_link_references(latex_ir: DocumentIR) -> None:
    """``\\ref{fig:x}`` must connect the float to the block that mentions it."""
    assert latex_ir.figures and latex_ir.figures[0].referenced_by
    assert latex_ir.tables and latex_ir.tables[0].referenced_by
    assert latex_ir.extra["latex"]["float_labels"]["f001"] == "fig:dokladnosc"


def test_macros_are_stripped_from_plain_text(latex_ir: DocumentIR) -> None:
    assert "\\cite" not in latex_ir.plain_text
    assert "\\emph" not in latex_ir.plain_text
    assert "\\includegraphics" not in latex_ir.plain_text
    assert "LaTeX" not in latex_ir.plain_text or "\\LaTeX" not in latex_ir.plain_text


def test_comments_do_not_reach_the_ir(tmp_path: Path) -> None:
    source = (
        "\\documentclass{article}\n\\begin{document}\n"
        "Tekst widoczny. % ten komentarz nie powinien trafić do IR\n"
        "\\end{document}\n"
    )
    path = tmp_path / "main.tex"
    path.write_text(source, encoding="utf-8")
    ir = LatexParser().parse(path, "doc-comment", "main.tex")
    assert "komentarz" not in ir.plain_text
    assert "Tekst widoczny." in ir.plain_text


def test_input_outside_the_root_is_refused(tmp_path: Path) -> None:
    """``\\input{/etc/passwd}`` / ``\\input{../../x}`` must never be followed."""
    secret = tmp_path / "secret.tex"
    secret.write_text("TAJNA TRESC", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    main = project / "main.tex"
    main.write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "Bezpieczny tekst.\n"
        "\\input{../secret}\n"
        "\\input{/etc/passwd}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    ir = LatexParser().parse(main, "doc-escape", "main.tex")
    assert "TAJNA TRESC" not in ir.plain_text
    assert "root" not in ir.plain_text
    assert ir.extra["latex"]["inputs_skipped"] == ["../secret", "/etc/passwd"]
    assert any("sandbox" in w for w in ir.extra["parse_warnings"])


def test_include_depth_is_capped(tmp_path: Path) -> None:
    """Self-including files must terminate (cycle guard + depth cap)."""
    main = tmp_path / "main.tex"
    main.write_text(
        "\\documentclass{article}\n\\begin{document}\nTekst.\n\\input{main}\n\\end{document}\n",
        encoding="utf-8",
    )
    ir = LatexParser().parse(main, "doc-cycle", "main.tex")
    assert "Tekst." in ir.plain_text
    assert any("cycle" in w or "depth" in w for w in ir.extra["parse_warnings"])


def test_line_numbers_are_reported(latex_ir: DocumentIR) -> None:
    for block in latex_ir.blocks:
        assert block.locator.line is not None and block.locator.line >= 1
        assert block.locator.xml_path  # the file the block came from
