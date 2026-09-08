"""Exclusion-mask construction (doc 01 §3.2, §4.1 step 4).

The single highest-leverage decision in the system: title page, statement,
abstracts, ToC, bibliography, appendices, code and quotations are never sent to
the similarity, grammar or AIGT modules. Skipping this is what makes commercial
tools report 80% false positives.

The mask is built from two independent sources so a failure in one does not
silently widen the analysed text:

1. **section kinds** (structure recovered by :mod:`app.ingestion.sections`);
2. **block-level signals** (``code`` / ``quote`` block types, ToC-shaped lines
   flagged by the parsers).
"""

from __future__ import annotations

from app.models.ir import DocumentIR, ExcludeReason, Exclusion

SECTION_REASON: dict[str, ExcludeReason] = {
    "title_page": "front_matter",
    "statement": "front_matter",
    "abstract_pl": "front_matter",
    "abstract_en": "front_matter",
    "toc": "toc",
    "bibliography": "bibliography",
    "appendix": "appendix",
}

BLOCK_REASON: dict[str, ExcludeReason] = {
    "code": "code",
    "quote": "quote",
}

EXCLUDED_FLAG = "excluded_from_analysis"


def _merge(ranges: list[tuple[int, int, ExcludeReason]]) -> list[Exclusion]:
    """Sort and merge touching/overlapping ranges of the same reason."""
    ranges = sorted((s, e, r) for s, e, r in ranges if e > s)
    merged: list[Exclusion] = []
    for start, end, reason in ranges:
        if merged and merged[-1].reason == reason and start <= merged[-1].end + 1:
            merged[-1].end = max(merged[-1].end, end)
            continue
        merged.append(Exclusion(start=start, end=end, reason=reason))
    return merged


def candidate_ranges(ir: DocumentIR) -> list[tuple[int, int, ExcludeReason]]:
    ranges: list[tuple[int, int, ExcludeReason]] = []
    by_id = {b.id: b for b in ir.blocks}

    for section in ir.sections:
        reason = SECTION_REASON.get(section.kind)
        if reason is None:
            continue
        for block_id in section.block_ids:
            block = by_id.get(block_id)
            if block is not None:
                ranges.append((block.span.start, block.span.end, reason))

    for block in ir.blocks:
        reason = BLOCK_REASON.get(block.type)
        if reason is not None:
            ranges.append((block.span.start, block.span.end, reason))
        elif "toc" in block.flags:
            ranges.append((block.span.start, block.span.end, "toc"))
    return ranges


def build_exclusion_mask(ir: DocumentIR) -> list[Exclusion]:
    return _merge(candidate_ranges(ir))


def mark_excluded_blocks(ir: DocumentIR) -> int:
    """Flag every block covered by the mask. Returns the number flagged.

    Downstream modules filter on the flag *and* the mask: the flag makes the
    UI able to show why a block was skipped without recomputing anything.
    """
    flagged = 0
    for block in ir.blocks:
        if ir.is_excluded(block.span.start, block.span.end):
            if EXCLUDED_FLAG not in block.flags:
                block.flags.append(EXCLUDED_FLAG)
            flagged += 1
    return flagged
