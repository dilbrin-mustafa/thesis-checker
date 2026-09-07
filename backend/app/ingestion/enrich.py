"""Post-parse enrichment: the shared tail of every parser (Phase 1, week 7).

Parsers are responsible for *structure and offsets only*. Everything that is
format-independent happens here, in a fixed order:

1. sections (structure → canonical kinds)
2. bibliography blocks retyped to ``bibitem``
3. bibliography entries harvested into ``references``
4. language detection (per section — abstracts are bilingual)
5. abstract kinds corrected by content language
6. exclusion mask (structure + block signals)
7. in-text citations and float cross-references

Keeping this in one place is what lets the three parsers stay small and lets
the IR contract test mean the same thing for all of them.
"""

from __future__ import annotations

import re

from app.ingestion.bibliography import harvest_references
from app.ingestion.citations import attach_citations
from app.ingestion.exclusions import build_exclusion_mask, mark_excluded_blocks
from app.ingestion.language import detect_languages
from app.ingestion.sections import build_sections
from app.models.ir import DocumentIR, Figure, Table

BIBITEM_SOURCE_TYPES: frozenset[str] = frozenset({"paragraph", "list_item"})
FLOAT_SCAN_TYPES: frozenset[str] = frozenset(
    {"paragraph", "list_item", "quote", "caption", "heading"}
)


def retype_bibliography_blocks(ir: DocumentIR) -> int:
    """Mark the entries inside a bibliography section as ``bibitem``."""
    bibliography_ids = {
        block_id
        for section in ir.sections
        if section.kind == "bibliography"
        for block_id in section.block_ids
    }
    if not bibliography_ids:
        return 0
    retyped = 0
    for block in ir.blocks:
        if block.id in bibliography_ids and block.type in BIBITEM_SOURCE_TYPES:
            block.type = "bibitem"
            retyped += 1
    return retyped


def _float_pattern(number: str) -> re.Pattern[str]:
    escaped = re.escape(number)
    return re.compile(
        rf"(?:rys\.?|rysunku|rysunek|rysunek|fig\.?|figure|wykres|"
        rf"tab\.?|tabeli|tabela|tabl\.?|table)\s*(?:nr\.?\s*)?{escaped}\b",
        re.IGNORECASE,
    )


def link_float_references(ir: DocumentIR) -> None:
    """Fill ``referenced_by`` for figures and tables.

    Every figure/table that no block mentions is a real editorial problem
    (PG rules require each float to be referenced in the text) — Phase 2 turns
    this into a finding, so the link has to exist by the end of Phase 1.
    """
    if not ir.figures and not ir.tables:
        return
    floats: list[Figure | Table] = [*ir.figures, *ir.tables]
    patterns = [
        (float_obj, _float_pattern(float_obj.number)) for float_obj in floats if float_obj.number
    ]
    if not patterns:
        return
    for block in ir.blocks:
        if block.type not in FLOAT_SCAN_TYPES:
            continue
        for float_obj, pattern in patterns:
            if pattern.search(block.text) and block.id not in float_obj.referenced_by:
                float_obj.referenced_by.append(block.id)


def fix_abstract_kinds(ir: DocumentIR) -> int:
    """An abstract's kind follows its *content* language, not its heading.

    A Polish thesis with ``\\begin{abstract}`` or an "Abstract" heading over
    Polish text is common; the reverse (an English summary under "Streszczenie")
    also occurs. Both map to front matter either way, so the exclusion mask is
    unaffected — but per-section language reporting and the Phase 2 abstract
    rules need the label to be true.
    """
    corrected = 0
    for section in ir.sections:
        if section.kind not in ("abstract_pl", "abstract_en"):
            continue
        lang = ir.language.per_section.get(section.id)
        if lang == "pl" and section.kind == "abstract_en":
            section.kind = "abstract_pl"
            corrected += 1
        elif lang == "en" and section.kind == "abstract_pl":
            section.kind = "abstract_en"
            corrected += 1
    return corrected


def enrich(ir: DocumentIR) -> DocumentIR:
    """Apply the full enrichment chain, in place, and return the IR."""
    ir.sections = build_sections(ir.blocks, source_format=ir.source.format)
    retype_bibliography_blocks(ir)
    harvest_references(ir)
    ir.language = detect_languages(ir)
    fix_abstract_kinds(ir)
    ir.exclusion_mask = build_exclusion_mask(ir)
    mark_excluded_blocks(ir)
    attach_citations(ir)
    link_float_references(ir)
    return ir


def enrichment_summary(ir: DocumentIR) -> dict[str, int | float]:
    """Compact stats for the CLI and the ingestion report."""
    analysed_chars = sum(
        len(block.text) for block in ir.blocks if "excluded_from_analysis" not in block.flags
    )
    total_chars = max(len(ir.plain_text), 1)
    return {
        "blocks": len(ir.blocks),
        "headings": sum(1 for b in ir.blocks if b.type == "heading"),
        "sections": len(ir.sections),
        "citations": len(ir.citations),
        "references": len(ir.references),
        "figures": len(ir.figures),
        "tables": len(ir.tables),
        "equations": len(ir.equations),
        "excluded_blocks": sum(1 for b in ir.blocks if "excluded_from_analysis" in b.flags),
        "analysed_char_ratio": round(analysed_chars / total_chars, 3),
    }
