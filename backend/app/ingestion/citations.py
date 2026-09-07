"""In-text citation detection and reference linking (doc 01 §4.1).

Recognises the four citation shapes that cover essentially all PL/EN
engineering theses:

* numeric in brackets — ``[12]``, ``[3, 7]``, ``[3-5]``
* numeric in parentheses — ``(12)`` is *not* matched (far too noisy in
  technical prose: equation numbers, list markers, years)
* author-year in parentheses — ``(Kowalski, 2020)``, ``(Smith and Jones 2019)``
* author-year in brackets — ``[Kowalski, 2020]``
* narrative author-year — ``Kowalski (2020) pokazuje…``

Every hit keeps its character offsets so a finding can point at the exact
``[12]`` in the original document, and so Phase 3 can build orphan/uncited
graphs without re-reading the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.ir import Citation, DocumentIR, Reference, Span

_NAME = r"[A-ZĄĆĘŁŃÓŚŹŻ][^\W\d_]{1,40}"
_YEAR = r"(?:1[6-9]\d{2}|20\d{2})[a-z]?"
_JOINER = r"(?:\s*(?:,|i|and|&|et al\.?|i in\.?)\s*)"

NUMERIC_RE = re.compile(r"\[(\d{1,3}(?:\s*[,;]\s*\d{1,3}|\s*[-–—]\s*\d{1,3})*)\]")
AUTHOR_YEAR_PAREN_RE = re.compile(
    rf"\((?P<authors>{_NAME}(?:{_JOINER}{_NAME}){{0,3}}),?\s+(?P<year>{_YEAR})\)"
)
AUTHOR_YEAR_BRACKET_RE = re.compile(
    rf"\[(?P<authors>{_NAME}(?:{_JOINER}{_NAME}){{0,3}}),?\s+(?P<year>{_YEAR})\]"
)
NARRATIVE_RE = re.compile(rf"\b(?P<authors>{_NAME})\s*\((?P<year>{_YEAR})\)")

#: Block types whose text is prose and may carry citations.
CITATION_BLOCK_TYPES: frozenset[str] = frozenset(
    {"paragraph", "list_item", "quote", "caption", "bibitem"}
)


@dataclass(frozen=True)
class CitationHit:
    raw: str
    style: str
    keys: tuple[str, ...]
    start: int
    end: int
    extra: dict[str, str] = field(default_factory=dict)


def _expand_numeric(inner: str) -> tuple[str, ...]:
    keys: list[str] = []
    for part in re.split(r"[,;]", inner):
        part = part.strip()
        if not part:
            continue
        range_match = re.fullmatch(r"(\d{1,3})\s*[-–—]\s*(\d{1,3})", part)
        if range_match:
            low, high = int(range_match.group(1)), int(range_match.group(2))
            if 0 < high - low <= 25:  # a 3-999 "range" is not a citation range
                keys.extend(str(n) for n in range(low, high + 1))
                continue
        if part.isdigit():
            keys.append(part)
    return tuple(keys)


def find_citations(text: str) -> list[CitationHit]:
    """All citation hits in *text*, in reading order, offsets included."""
    hits: list[CitationHit] = []
    claimed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < c_end and c_start < end for c_start, c_end in claimed)

    for match in NUMERIC_RE.finditer(text):
        keys = _expand_numeric(match.group(1))
        if not keys:
            continue
        hits.append(
            CitationHit(
                raw=match.group(0),
                style="numeric",
                keys=keys,
                start=match.start(),
                end=match.end(),
            )
        )
        claimed.append((match.start(), match.end()))

    for pattern in (AUTHOR_YEAR_PAREN_RE, AUTHOR_YEAR_BRACKET_RE):
        for match in pattern.finditer(text):
            if overlaps(match.start(), match.end()):
                continue
            authors = re.sub(r"\s+", " ", match.group("authors")).strip()
            year = match.group("year")
            hits.append(
                CitationHit(
                    raw=match.group(0),
                    style="author_year",
                    keys=(f"{authors}, {year}",),
                    start=match.start(),
                    end=match.end(),
                    extra={"authors": authors, "year": year},
                )
            )
            claimed.append((match.start(), match.end()))

    for match in NARRATIVE_RE.finditer(text):
        if overlaps(match.start(), match.end()):
            continue
        # "Rys. 3 (2020)" is not a citation; require a plausible surname.
        if len(match.group("authors")) < 3:
            continue
        hits.append(
            CitationHit(
                raw=match.group(0),
                style="author_year",
                keys=(f"{match.group('authors')}, {match.group('year')}",),
                start=match.start(),
                end=match.end(),
                extra={"authors": match.group("authors"), "year": match.group("year")},
            )
        )
        claimed.append((match.start(), match.end()))

    hits.sort(key=lambda h: h.start)
    return hits


def _numeric_index(references: list[Reference]) -> dict[str, str]:
    """Bibliography order → reference id (numeric citation styles)."""
    return {str(position): ref.id for position, ref in enumerate(references, start=1)}


def _surname_year_index(references: list[Reference]) -> dict[tuple[str, int], str]:
    index: dict[tuple[str, int], str] = {}
    for ref in references:
        if not ref.parsed.year:
            continue
        for author in ref.parsed.authors:
            surname = author.split(",")[0].strip().split()[-1] if author.strip() else ""
            surname = re.sub(r"[^\w]", "", surname).lower()
            if surname:
                index.setdefault((surname, ref.parsed.year), ref.id)
    return index


def resolve_ref_id(
    hit: CitationHit,
    *,
    numeric: dict[str, str],
    surname_year: dict[tuple[str, int], str],
) -> str | None:
    """Best-effort link from an in-text hit to a bibliography entry."""
    if hit.style == "numeric":
        return numeric.get(hit.keys[0]) if hit.keys else None
    authors = hit.extra.get("authors", "")
    year = hit.extra.get("year", "")
    if not year.isdigit():
        return None
    first_author = re.split(r",|\s+(?:i|and|&|et al)\b", authors, maxsplit=1)[0].strip()
    surname = first_author.split()[-1] if first_author else ""
    surname = re.sub(r"[^\w]", "", surname).lower()
    if not surname:
        return None
    return surname_year.get((surname, int(year)))


def attach_citations(ir: DocumentIR, *, skip_excluded: bool = True) -> list[Citation]:
    """Extract in-text citations and write them into *ir* (spans are absolute).

    Citations a parser already resolved (LaTeX knows the BibTeX keys, so its
    links are exact) are kept; this pass only adds what the text reveals, and
    skips any span the parser has already claimed.
    """
    numeric = _numeric_index(ir.references)
    surname_year = _surname_year_index(ir.references)
    citations: list[Citation] = list(ir.citations)
    taken_ids = {citation.id for citation in citations}
    taken_spans = {(c.block_id, c.span.start, c.span.end) for c in citations}
    seq = len(citations)
    for block in ir.blocks:
        if block.type not in CITATION_BLOCK_TYPES:
            continue
        if skip_excluded and ir.is_excluded(block.span.start, block.span.end):
            continue
        for hit in find_citations(block.text):
            start = block.span.start + hit.start
            end = block.span.start + hit.end
            if (block.id, start, end) in taken_spans:
                continue
            seq += 1
            while f"c{seq:04d}" in taken_ids:
                seq += 1
            citations.append(
                Citation(
                    id=f"c{seq:04d}",
                    raw=hit.raw,
                    style=hit.style,
                    block_id=block.id,
                    span=Span(start=start, end=end),
                    ref_id=resolve_ref_id(hit, numeric=numeric, surname_year=surname_year),
                )
            )
            taken_ids.add(f"c{seq:04d}")
    ir.citations = citations
    return citations
