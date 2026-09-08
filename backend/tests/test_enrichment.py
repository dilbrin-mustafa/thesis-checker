"""Enrichment tests (Phase 1, week 7).

These are the units the whole pipeline shares, and the ones where a silent
mistake is expensive: a sentence splitter that breaks on "prof." corrupts
similarity shingling, grammar windows and every AIGT measurement downstream.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.ingestion.bibliography import bibliography_block_ids, harvest_references
from app.ingestion.citations import find_citations
from app.ingestion.enrich import enrich, enrichment_summary, fix_abstract_kinds
from app.ingestion.exclusions import build_exclusion_mask, mark_excluded_blocks
from app.ingestion.language import detect_text_language
from app.ingestion.references import confidence_for, extract_pages, parse_reference
from app.ingestion.sections import build_sections, classify_heading, looks_like_toc_block
from app.ingestion.sentences import Segment, segment_document, split_sentences
from app.models.ir import Block, DocumentIR, Language, Section, Source, Span


# -- sentences ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Praca opisuje metody. Wyniki są dobre.", 2),
        ("Prof. Nowak opisał metodę. Zastosowano ją później.", 2),
        ("Zobacz rys. 3.2 na stronie 12.", 1),
        ("Wartość wynosi 3.14 oraz 1,5 metra.", 1),
        ("To jest tzn. przykład. Kolejne zdanie.", 2),
        ("Metodę opisano w (Kowalski, 2020). Następne zdanie zaczyna się tutaj.", 2),
        ("Pierwsze zdanie! Drugie? Trzecie.", 3),
        ("Adres to https://example.com/a.b/c. Kolejne zdanie.", 2),
        ("DOI 10.1000/xyz.123 jest poprawny. Następne zdanie.", 2),
        ("„Cytat kończy się tutaj.” Następne zdanie zaczyna się od wielkiej.", 2),
        ("J. Kowalski oraz A. Nowak opisali metodę. Wyniki są zgodne.", 2),
        ("Autorzy (m.in. Nowak) piszą o tym. Potem opisują wyniki badań.", 2),
        ("", 0),
        ("   ", 0),
    ],
)
def test_sentence_splitter(text: str, expected: int) -> None:
    assert len(split_sentences(text)) == expected


def test_sentence_offsets_slice_back_to_the_text() -> None:
    text = "Pierwsze zdanie. Drugie zdanie!"
    for sentence in split_sentences(text):
        assert text[sentence.start : sentence.end] == sentence.text


def test_segmentation_uses_absolute_offsets(parsed: dict[str, DocumentIR]) -> None:
    ir = parsed["docx"]
    segments = segment_document(ir)
    assert segments
    assert all(isinstance(s, Segment) for s in segments)
    for segment in segments:
        assert ir.plain_text[segment.start : segment.end] == segment.text
    # excluded content is never segmented
    assert ir.exclusion_mask
    for segment in segments:
        assert not ir.is_excluded(segment.start, segment.end)


# -- language -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Niniejsza praca opisuje metody automatycznej analizy tekstów naukowych "
            "oraz przedstawia wyniki przeprowadzonych eksperymentów badawczych.",
            "pl",
        ),
        (
            "This thesis describes the methods used for automatic analysis of academic "
            "texts and presents the results of the performed experiments.",
            "en",
        ),
        ("za krótki", None),
    ],
)
def test_language_detection(text: str, expected: str | None) -> None:
    assert detect_text_language(text) == expected


def test_bilingual_abstracts_are_labelled_separately(parsed: dict[str, DocumentIR]) -> None:
    ir = parsed["docx"]
    assert ir.language.primary == "pl"
    kinds = {s.id: s.kind for s in ir.sections}
    en_section = next(s for s in ir.sections if s.kind == "abstract_en")
    pl_section = next(s for s in ir.sections if s.kind == "abstract_pl")
    assert ir.language.per_section[en_section.id] == "en"
    assert ir.language.per_section[pl_section.id] == "pl"
    assert kinds[en_section.id] == "abstract_en"


def test_abstract_kind_follows_content_language() -> None:
    """A Polish abstract under an "Abstract" heading is still abstract_pl."""
    ir = DocumentIR(
        doc_id="d1",
        source=Source(format="latex", filename="t.tex", sha256="a" * 64, pages=0, words=10),
        language=Language(primary="pl", per_section={"s0001": "pl"}),
        plain_text="Abstract",
        blocks=[
            Block(id="b0001", type="heading", level=1, text="Abstract", span=Span(start=0, end=8))
        ],
        sections=[Section(id="s0001", kind="abstract_en", title="Abstract", block_ids=["b0001"])],
    )
    assert fix_abstract_kinds(ir) == 1
    assert ir.sections[0].kind == "abstract_pl"


# -- sections -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Wstęp", "introduction"),
        ("1. Wprowadzenie", "introduction"),
        ("2.1. Metodyka badań", "methodology"),
        ("Przegląd literatury", "literature_review"),
        ("Stan wiedzy", "literature_review"),
        ("Wyniki eksperymentów", "results"),
        ("Podsumowanie i wnioski", "summary"),
        ("Bibliografia", "bibliography"),
        ("Spis treści", "toc"),
        ("Dodatek A", "appendix"),
        ("Oświadczenie autora", "statement"),
        ("Abstract", "abstract_en"),
        ("Streszczenie", "abstract_pl"),
        ("Politechnika Gdańska", "title_page"),
        ("Coś zupełnie innego", None),
    ],
)
def test_section_lexicon(title: str, kind: str | None) -> None:
    assert classify_heading(title) == kind


def test_toc_lines_are_recognised() -> None:
    assert looks_like_toc_block("1. Wstęp ......... 5")
    assert looks_like_toc_block("2. Metodyka\t9")
    assert not looks_like_toc_block("To jest zwykłe zdanie w pracy dyplomowej.")


def test_sections_are_cut_at_the_shallowest_heading(parsed: dict[str, DocumentIR]) -> None:
    ir = parsed["latex"]
    kinds = [s.kind for s in ir.sections]
    assert kinds[0] == "abstract_pl"
    assert "introduction" in kinds and "methodology" in kinds
    # subsections do not split sections when a shallower level exists
    article = parsed["latex_article"]
    assert "introduction" in [s.kind for s in article.sections]


# -- exclusions ---------------------------------------------------------------
def test_exclusion_mask_covers_front_matter_toc_and_bibliography(
    parsed: dict[str, DocumentIR],
) -> None:
    ir = parsed["docx"]
    reasons = {e.reason for e in ir.exclusion_mask}
    assert {"front_matter", "toc", "bibliography", "code", "quote"} <= reasons


def test_excluded_blocks_are_flagged(parsed: dict[str, DocumentIR]) -> None:
    ir = parsed["docx"]
    for block in ir.blocks:
        inside = ir.is_excluded(block.span.start, block.span.end)
        assert inside == ("excluded_from_analysis" in block.flags)


def test_mask_ranges_never_overlap() -> None:
    ir = _mini_ir()
    ir.sections = build_sections(ir.blocks, source_format="docx")
    mask = build_exclusion_mask(ir)
    for previous, following in pairwise(mask):
        assert previous.end < following.start
    assert mark_excluded_blocks(ir) >= 0


def test_analysed_text_excludes_the_bibliography(parsed: dict[str, DocumentIR]) -> None:
    ir = parsed["docx"]
    summary = enrichment_summary(ir)
    assert 0.0 < summary["analysed_char_ratio"] < 1.0
    assert summary["excluded_blocks"] > 0


# -- citations ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Metodę opisano w [1].", ["[1]"]),
        ("Zobacz [2, 3] oraz [4-6].", ["[2, 3]", "[4-6]"]),
        ("Według (Kowalski, 2020) wynika to z badań.", ["(Kowalski, 2020)"]),
        ("Smith and Jones (2019) opisali to zjawisko dokładnie.", ["Jones (2019)"]),
        ("Brak cytatu w tym zdaniu.", []),
        ("Rysunek 3 (a) nie jest cytatem.", []),
    ],
)
def test_citation_detection(text: str, expected: list[str]) -> None:
    assert [hit.raw for hit in find_citations(text)] == expected


def test_numeric_ranges_expand() -> None:
    hit = find_citations("Zobacz [4-6].")[0]
    assert hit.keys == ("4", "5", "6")


def test_citation_spans_are_absolute(parsed: dict[str, DocumentIR]) -> None:
    ir = parsed["docx"]
    numeric = [c for c in ir.citations if c.style == "numeric"]
    assert numeric
    for citation in numeric:
        assert ir.plain_text[citation.span.start : citation.span.end] == citation.raw
        block = next(b for b in ir.blocks if b.id == citation.block_id)
        assert block.span.start <= citation.span.start < citation.span.end <= block.span.end
    # in-range citations resolve; the deliberately dangling [4-6] does not
    assert any(c.ref_id == ir.references[0].id for c in numeric)


# -- references ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "year", "doi", "author_fragment"),
    [
        (
            "[1] Kowalski J.: Metody analizy tekstu. Wydawnictwo PG, Gdańsk 2020.",
            2020,
            None,
            "Kowalski",
        ),
        (
            "Smith J., Jones A.: Neural detection. Journal of AI, vol. 12, 2019, "
            "s. 3-15. doi:10.1000/xyz123",
            2019,
            "10.1000/xyz123",
            "Smith",
        ),
        ("Nowak, A. (2021). Wykrywanie plagiatów. Zeszyty Naukowe PG.", 2021, None, "Nowak"),
    ],
)
def test_reference_parsing(raw: str, year: int, doi: str | None, author_fragment: str) -> None:
    parsed_reference, confidence = parse_reference(raw)
    assert parsed_reference.year == year
    assert parsed_reference.doi == doi
    assert any(author_fragment.lower() in a.lower() for a in parsed_reference.authors)
    assert parsed_reference.title
    assert 0.0 < confidence <= 1.0


def test_confidence_reflects_completeness() -> None:
    sparse, sparse_confidence = parse_reference("Tytuł pracy.")
    _rich, rich_confidence = parse_reference(
        "Kowalski J.: Tytuł pracy. Journal of AI, 2020. doi:10.1000/abc"
    )
    assert rich_confidence > sparse_confidence
    assert confidence_for(sparse) == sparse_confidence


def test_page_ranges_are_extracted() -> None:
    assert extract_pages("s. 3-15") == ("3", "15")
    assert extract_pages("pp. 120-131") == ("120", "131")
    assert extract_pages("brak stron") is None


# -- bibliography harvesting --------------------------------------------------
def test_no_bibliography_means_no_references() -> None:
    """The safety property: never treat the whole document as references."""
    ir = _mini_ir()
    ir.sections = build_sections(ir.blocks, source_format="docx")
    assert bibliography_block_ids(ir) == set()
    assert harvest_references(ir) == []
    assert ir.references == []


def test_bibliography_heading_fallback(parsed: dict[str, DocumentIR]) -> None:
    """Even if section classification missed it, a "Bibliografia" heading works."""
    ir = parsed["pdf_no_toc"]
    assert bibliography_block_ids(ir)
    assert ir.references


def test_enrich_is_idempotent_enough(parsed: dict[str, DocumentIR]) -> None:
    """Re-running the chain must not duplicate citations or blow up offsets."""
    ir = parsed["docx"]
    before = len(ir.citations)
    enrich(ir)
    assert len(ir.citations) == before
    for block in ir.blocks:
        assert ir.plain_text[block.span.start : block.span.end] == block.text


# -- helpers ------------------------------------------------------------------
def _mini_ir() -> DocumentIR:
    """A tiny hand-built IR: two paragraphs, no headings."""
    text = "Pierwszy akapit pracy.\nDrugi akapit pracy."
    first, second = text.split("\n")
    return DocumentIR(
        doc_id="d-mini",
        source=Source(format="docx", filename="mini.docx", sha256="b" * 64, pages=1, words=6),
        language=Language(primary="pl"),
        plain_text=text,
        blocks=[
            Block(id="b0001", type="paragraph", text=first, span=Span(start=0, end=len(first))),
            Block(
                id="b0002",
                type="paragraph",
                text=second,
                span=Span(start=len(first) + 1, end=len(text)),
            ),
        ],
    )
