"""PL/EN-aware sentence segmentation (Phase 1, week 7).

A naive ``text.split(".")`` splitter breaks on ``prof.``, ``rys.``, ``tzn.``,
``m.in.``, ``et al.``, decimal separators and initials — all extremely common
in Polish academic prose. Every downstream module that works per sentence
(similarity shingling, grammar, AIGT windowing, citation-context support)
depends on this being right, so the abbreviation table is a first-class
artefact rather than an afterthought.

Returns offsets relative to the input string so callers can lift them into
``DocumentIR.plain_text`` coordinates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.ir import DocumentIR

#: Abbreviations that may carry a terminal period without ending a sentence.
#: Lowercased for matching; Polish first because it is the primary locale.
ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # Polish
        "prof",
        "dr",
        "hab",
        "inż",
        "mgr",
        "p",
        "s",
        "ss",
        "r",
        "w",
        "ww",
        "np",
        "tj",
        "tzn",
        "itd",
        "itp",
        "m.in",
        "rys",
        "tab",
        "fig",
        "nr",
        "ang",
        "ds",
        "por",
        "zesz",
        "b.r",
        "ok",
        "str",
        "poz",
        "tom",
        "vol",
        "wyd",
        "n.p.m",
        "p.n.e",
        "n.e",
        "zob",
        "cdn",
        "jw",
        "bw",
        "tzw",
        "godz",
        # English
        "figs",
        "eq",
        "eqs",
        "sect",
        "ch",
        "chap",
        "e.g",
        "i.e",
        "cf",
        "et al",
        "al",
        "no",
        "nos",
        "pp",
        "vols",
        "jr",
        "sr",
        "mr",
        "mrs",
        "ms",
        "vs",
        "etc",
        "approx",
        "dept",
        "univ",
        "ed",
        "eds",
        "trans",
        "inc",
        "ltd",
        "co",
        "st",
        "ave",
    }
)

# Region we never want to split inside.
#: Regions that may contain a period without ending a sentence. The trailing
#: ``(?<![.,;:])`` matters: a URL at the end of a sentence keeps that final
#: period as a real sentence terminator.
_PROTECTED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d+[.,]\d+\b"),  # 3.14 / 1,5 (Polish decimal comma)
    re.compile(r"\b(?:https?://|doi:|www\.)[^\s,;\x22\x27„”\)\]>]+(?<![.,;:])", re.IGNORECASE),
    re.compile(r"\b10\.\d{4,9}/[^\s,;\x22\x27„”\)\]>]+(?<![.,;:])"),  # DOI
    re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"),  # dates
    re.compile(r"\.\s*\.\s*\."),  # . . . spaced ellipsis
    re.compile(r"(?<=\b[A-ZĄĆĘŁŃÓŚŹŻ])\.(?=\s+[A-ZĄĆĘŁŃÓŚŹŻ])"),  # "J. Kowalski"
)

_TERMINATOR = re.compile(r"[.!?…]{1,3}")
_MASK = "\ue000"  # private-use char: cannot collide with document text


def _abbrev_end(text: str, dot_index: int) -> bool:
    """True if the period at *dot_index* closes a known abbreviation."""
    start = dot_index
    # walk back over the token (letters, dots for multi-part forms like "m.in")
    while start > 0 and (text[start - 1].isalpha() or text[start - 1] in ".-"):
        start -= 1
    token = text[start:dot_index].lower().strip(".-")
    if not token:
        return False
    return token in ABBREVIATIONS


def _mask_protected(text: str) -> str:
    masked = list(text)
    for pattern in _PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            for i in range(match.start(), match.end()):
                masked[i] = _MASK
    return "".join(masked)


@dataclass(frozen=True)
class Sentence:
    start: int
    end: int
    text: str


def split_sentences(text: str, min_chars: int = 2) -> list[Sentence]:
    """Split *text* into sentences with offsets. Never raises."""
    if not text.strip():
        return []
    masked = _mask_protected(text)
    boundaries: list[int] = []
    for match in _TERMINATOR.finditer(masked):
        dot = match.end() - 1
        if masked[dot] == "." and _abbrev_end(masked, dot):
            continue
        rest = masked[match.end() :]
        if rest == "":
            continue
        closing = re.match(r"[\s\"”’»\)\]]+", rest)
        if not closing:
            continue
        after = rest[closing.end() :]
        if after == "":
            continue
        nxt = after[0]
        # A sentence ends when the next token starts a new sentence: a capital,
        # a digit (numbered lists), an opening quote/dash, or a lowercase
        # conjunction after "!" / "?" (rare but real).
        if nxt.isupper() or nxt.isdigit() or nxt in "\"„'“«(-–—" or masked[dot] in "!?…":
            boundaries.append(match.end())

    sentences: list[Sentence] = []
    cursor = 0
    for boundary in [*boundaries, len(text)]:
        chunk = text[cursor:boundary]
        stripped = chunk.strip()
        if len(stripped) >= min_chars:
            offset = cursor + (len(chunk) - len(chunk.lstrip()))
            sentences.append(Sentence(offset, offset + len(stripped), stripped))
        cursor = boundary
    return sentences


@dataclass(frozen=True)
class Segment:
    """A sentence anchored in ``DocumentIR.plain_text``.

    Phase 1 produces these in memory; the ``segments`` table they persist to
    lands with the database work (doc 03 §5). Analysers get them from the
    segmenter directly, so the offsets are identical either way.
    """

    block_id: str
    start: int
    end: int
    text: str
    word_count: int


#: Block types worth segmenting: the rest is structure or excluded content.
SEGMENTABLE_TYPES: frozenset[str] = frozenset(
    {"paragraph", "list_item", "caption", "bibitem", "quote"}
)


def segment_document(ir: DocumentIR, *, respect_exclusions: bool = True) -> list[Segment]:
    """Sentence-segment every analysable block of *ir*."""
    out: list[Segment] = []
    for block in ir.blocks:
        if block.type not in SEGMENTABLE_TYPES:
            continue
        if "excluded_from_analysis" in block.flags:
            continue
        if respect_exclusions and ir.is_excluded(block.span.start, block.span.end):
            continue
        for sentence in split_sentences(block.text):
            start = block.span.start + sentence.start
            out.append(
                Segment(
                    block_id=block.id,
                    start=start,
                    end=block.span.start + sentence.end,
                    text=sentence.text,
                    word_count=len(sentence.text.split()),
                )
            )
    return out
