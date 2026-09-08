"""Bibliography harvesting: ``bibitem`` blocks → ``Reference`` objects.

DOCX and PDF give us a bibliography section but no structure inside it; LaTeX
gives us a ``.bib``. This module normalises both into ``ir.references`` so
citation linking (``[12]`` → entry 12) and the Phase 3 resolver work the same
way regardless of source format.

Entries the parser already parsed (LaTeX, from ``.bib``) are kept untouched —
structured metadata beats a regex, and overwriting it would lose the BibTeX key
that makes ``ref_id`` links exact.
"""

from __future__ import annotations

from app.ingestion.references import parse_reference
from app.ingestion.sections import classify_heading
from app.models.ir import DocumentIR, Reference

#: Only these block types can be bibliography entries.
ENTRY_TYPES: frozenset[str] = frozenset({"bibitem", "paragraph", "list_item"})

#: Minimum length for something to be a reference rather than a stray line.
MIN_ENTRY_CHARS = 12


def harvest_references(ir: DocumentIR) -> list[Reference]:
    """Fill ``ir.references`` from the bibliography section, if not already set.

    Returns the reference list (also stored on the IR).
    """
    if ir.references:
        return ir.references

    bibliography_ids = bibliography_block_ids(ir)
    if not bibliography_ids:
        # No bibliography found: harvesting nothing is the only safe answer.
        # Falling back to "every paragraph is a reference" produces a report
        # made entirely of false positives.
        return []
    references: list[Reference] = []
    for block in ir.blocks:
        if block.type not in ENTRY_TYPES:
            continue
        if block.id not in bibliography_ids:
            continue
        if block.type == "heading":
            continue
        raw = block.text.strip()
        if len(raw) < MIN_ENTRY_CHARS:
            continue
        parsed, confidence = parse_reference(raw)
        seq = len(references) + 1
        references.append(
            Reference(id=f"r{seq:03d}", raw=raw, parsed=parsed, confidence=confidence)
        )
    ir.references = references
    return references


def bibliography_block_ids(ir: DocumentIR) -> set[str]:
    """Block ids that belong to the bibliography.

    Primary source: a section classified as ``bibliography``. Fallback: the
    first *heading* whose title matches the bibliography lexicon, at any level
    — PDF heading levels are induced and sometimes wrong, but the title is
    usually right, and everything after it is references.
    """
    ids = {
        block_id
        for section in ir.sections
        if section.kind == "bibliography"
        for block_id in section.block_ids
    }
    if ids:
        return ids

    for index, block in enumerate(ir.blocks):
        if block.type != "heading":
            continue
        if classify_heading(block.text) == "bibliography":
            return {b.id for b in ir.blocks[index:]}
    return set()
