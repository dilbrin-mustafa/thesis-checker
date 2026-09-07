"""IR contract tests for all three parsers (Phase 1 exit criterion #1).

Phase 0 froze the contract; this file is where every parser is held to it.
"""

from __future__ import annotations

import pytest

from app.ingestion.registry import PARSERS, detect_format, parse_document
from app.models.ir import DocumentIR
from tests.conftest import CorpusEntry
from tests.test_ir_contract import assert_valid_ir

DOCUMENT_KEYS = ("docx", "docx_manual", "pdf", "pdf_no_toc", "latex", "latex_article")


@pytest.mark.parametrize("key", DOCUMENT_KEYS)
def test_parser_output_satisfies_the_contract(parsed: dict[str, DocumentIR], key: str) -> None:
    assert_valid_ir(parsed[key])


@pytest.mark.parametrize("key", DOCUMENT_KEYS)
def test_block_offsets_reproduce_text_exactly(parsed: dict[str, DocumentIR], key: str) -> None:
    ir = parsed[key]
    assert ir.blocks, "no blocks parsed"
    for block in ir.blocks:
        assert ir.plain_text[block.span.start : block.span.end] == block.text, block.id


@pytest.mark.parametrize("key", DOCUMENT_KEYS)
def test_citation_spans_point_at_the_raw_citation(parsed: dict[str, DocumentIR], key: str) -> None:
    """The property the whole highlighting story rests on."""
    ir = parsed[key]
    assert ir.citations, "no citations parsed"
    for citation in ir.citations:
        sliced = ir.plain_text[citation.span.start : citation.span.end]
        if ir.source.format == "latex":
            # LaTeX renders \cite{a,b} as "[a, b]" so the span is highlightable
            for token in sliced.strip("[]").split(","):
                assert token.strip() in citation.raw
        else:
            assert sliced == citation.raw


@pytest.mark.parametrize("key", DOCUMENT_KEYS)
def test_exclusion_ranges_are_inside_the_document(parsed: dict[str, DocumentIR], key: str) -> None:
    ir = parsed[key]
    for exclusion in ir.exclusion_mask:
        assert 0 <= exclusion.start <= exclusion.end <= len(ir.plain_text)


@pytest.mark.parametrize("key", DOCUMENT_KEYS)
def test_sections_partition_real_blocks(parsed: dict[str, DocumentIR], key: str) -> None:
    ir = parsed[key]
    known = {b.id for b in ir.blocks}
    for section in ir.sections:
        assert section.block_ids, "section with no blocks"
        for block_id in section.block_ids:
            assert block_id in known


@pytest.mark.parametrize("key", DOCUMENT_KEYS)
def test_ir_survives_a_json_round_trip(parsed: dict[str, DocumentIR], key: str) -> None:
    """What the API will serialise in Phase 2 must validate on the way back."""
    ir = parsed[key]
    again = DocumentIR.model_validate(ir.model_dump(mode="json"))
    assert again.plain_text == ir.plain_text
    assert len(again.blocks) == len(ir.blocks)
    assert_valid_ir(again)


def test_every_format_has_a_parser() -> None:
    assert set(PARSERS) == {"docx", "latex", "pdf"}


@pytest.mark.parametrize(
    ("key", "expected"),
    [("docx", "docx"), ("pdf", "pdf"), ("latex", "latex")],
)
def test_format_detection_from_magic_bytes(
    corpus: dict[str, CorpusEntry], key: str, expected: str
) -> None:
    assert detect_format(corpus[key]["path"]) == expected


def test_sandboxed_and_unsandboxed_parses_agree(corpus: dict[str, CorpusEntry]) -> None:
    """The security boundary must not change the result (doc 02 §3)."""
    path = corpus["docx"]["path"]
    inside = parse_document(path, sandbox=False)
    outside = parse_document(path, sandbox=True)
    assert outside.plain_text == inside.plain_text
    assert len(outside.blocks) == len(inside.blocks)
    assert [b.type for b in outside.blocks] == [b.type for b in inside.blocks]
