"""Reference parsing: raw bibliography string → structured fields.

Phase 1 ships the deterministic regex/heuristic layer (doc 01 §4.1 step 5,
"GROBID if you can run it, else CRF/regex"). It is deliberately conservative:
every field is optional and ``Reference.confidence`` reports how much of the
entry we actually understood, so Phase 3 can decide what is worth sending to
Crossref instead of guessing.

Handles the shapes that dominate PL/EN engineering theses:

* ``Kowalski J., Nowak A.: Tytuł artykułu. „Journal of X", vol. 12, 2020, s. 3-10.``
* ``Smith, J. (2019). A title. In Proceedings of X. https://doi.org/10.1000/x``
* ``[12] Nowak A.: Metody. Wydawnictwo PG, Gdańsk 2018.``
* ``@article``-derived strings produced by the LaTeX parser.
"""

from __future__ import annotations

import re

from app.models.ir import ParsedReference

YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;„”\"'\)\]]+)")
URL_RE = re.compile(r"\b(https?://[^\s,;„”\"'\)\]>]+)")
ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5})", re.IGNORECASE)
PAGES_RE = re.compile(r"\b(?:s\.?|pp?\.?|str\.?)\s*([\dIVXLC]+)\s*[-–—]\s*([\dIVXLC]+)")
ISBN_RE = re.compile(r"\bISBN\s*[:\-]?\s*[\d\-X]{10,17}", re.IGNORECASE)
LEADING_INDEX_RE = re.compile(r"^\s*[\[\(]?\d{1,3}[\]\).,]\s*")
NOISE_RE = re.compile(
    r"\b(?:ISBN|ISSN|vol\.?|nr\.?|no\.?|tom|wyd\.?|ed\.?|s\.?|pp?\.?)\b", re.IGNORECASE
)

_QUOTE_PAIRS = (("„", "”"), ("“", "”"), ('"', '"'), ("'", "'"), ("«", "»"))

#: Words that indicate a venue/publisher rather than a title.
VENUE_HINTS = (
    "proceedings",
    "conference",
    "journal",
    "transactions",
    "review",
    "letters",
    "press",
    "publishing",
    "publisher",
    "wydawnictwo",
    "wydaw",
    "springer",
    "ieee",
    "acm",
    "elsevier",
    "wiley",
    "pwn",
    "pwe",
    "wn pwn",
    "university",
    "politechnika",
    "uniwersytet",
    "arxiv",
    "preprint",
    "raport",
    "report",
    "norma",
    "standard",
)


def _strip_quotes(text: str) -> str:
    text = text.strip()
    for opening, closing in _QUOTE_PAIRS:
        if text.startswith(opening) and text.endswith(closing) and len(text) > 2:
            return text[1:-1].strip()
    return text


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t\n,;:.")


def _split_authors(blob: str) -> list[str]:
    """Split an author blob on ';', ' and ', '&', or name-shaped commas."""
    blob = _clean(blob)
    if not blob:
        return []
    if ";" in blob:
        parts = [p for p in (chunk.strip(" .,") for chunk in blob.split(";")) if p]
        if len(parts) > 1:
            return parts
    blob = re.sub(r"\s+(?:and|or|i|&)\s+", "; ", blob, flags=re.IGNORECASE)
    if ";" in blob:
        parts = [p for p in (chunk.strip(" .,") for chunk in blob.split(";")) if p]
        if len(parts) > 1:
            return parts
    # "Kowalski J., Nowak A." — split on commas where a name shape follows.
    chunks = [c.strip(" .") for c in blob.split(",")]
    if len(chunks) > 1:
        name_like = re.compile(r"^[A-ZĄĆĘŁŃÓŚŹŻ][\w'’\-]*(\s+[A-ZĄĆĘŁŃÓŚŹŻ]\.?)")
        if all(name_like.match(c) for c in chunks if c):
            return [c for c in chunks if c]
    return [blob] if blob else []


def _find_year(text: str) -> tuple[int | None, int | None]:
    """Return (year, span_start). Prefers a bracketed/parenthesised year."""
    bracketed = re.search(r"[\(\[]\s*((?:1[6-9]|20)\d{2})[a-z]?\s*[\)\]]", text)
    if bracketed:
        return int(bracketed.group(1)), bracketed.start()
    match = YEAR_RE.search(text)
    if match:
        return int(match.group(1)), match.start()
    return None, None


def parse_reference(raw: str) -> tuple[ParsedReference, float]:
    """Parse one bibliography entry. Returns ``(parsed, confidence)``."""
    text = _clean(raw)
    if not text:
        return ParsedReference(), 0.0

    body = LEADING_INDEX_RE.sub("", text)

    doi_match = DOI_RE.search(body)
    doi = doi_match.group(1).rstrip(".") if doi_match else None
    url_match = URL_RE.search(body)
    url = url_match.group(1).rstrip(".,;") if url_match else None
    if url is None:
        arxiv = ARXIV_RE.search(body)
        if arxiv:
            url = f"https://arxiv.org/abs/{arxiv.group(1)}"

    year, year_pos = _find_year(body)

    # Author segment: everything before the year, cut at the first colon.
    head = body[:year_pos] if year_pos is not None else body
    if ":" in head:
        author_blob, _, rest_head = head.partition(":")
    else:
        author_blob, rest_head = head, ""
    authors = _split_authors(author_blob)
    if len(authors) > 6:  # not an author list — probably a title
        authors, rest_head = [], _clean(f"{author_blob}: {rest_head}" if rest_head else author_blob)

    tail = body[year_pos:] if year_pos is not None else ""
    tail_after_year = re.sub(r"^[\(\[]?\s*\d{4}[a-z]?\s*[\)\]]?", "", tail).strip(" .,;:")

    title = _strip_quotes(_clean(rest_head))
    venue: str | None = None

    if not title:
        # no colon form: "Kowalski J. Tytuł pracy. Wydawnictwo, 2020"
        chunks = [c.strip() for c in head.split(".") if c.strip()]
        if len(chunks) >= 2:
            authors = authors or _split_authors(chunks[0])
            title = _strip_quotes(chunks[1])
            venue = _clean(".".join(chunks[2:])) or None
        elif chunks:
            title = _strip_quotes(chunks[0])

    if venue is None and tail_after_year:
        candidate = re.split(r"[.;]", tail_after_year, maxsplit=1)[0]
        candidate = _clean(candidate)
        if candidate and len(candidate) < 160:
            venue = candidate

    if title and venue and venue.lower() in title.lower() and len(venue.split()) <= 2:
        venue = None
    if venue and NOISE_RE.fullmatch(venue):
        venue = None

    parsed = ParsedReference(
        authors=authors,
        title=title or None,
        year=year,
        venue=venue,
        doi=doi,
        url=url,
    )
    return parsed, confidence_for(parsed)


def confidence_for(parsed: ParsedReference) -> float:
    """Fraction of the fields Phase 3 needs in order to resolve the entry."""
    score = 0.0
    if parsed.title:
        score += 0.35
    if parsed.authors:
        score += 0.25
    if parsed.year:
        score += 0.2
    if parsed.doi:
        score += 0.15
    if parsed.venue:
        score += 0.05
    return round(min(score, 1.0), 2)


def looks_like_venue(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in VENUE_HINTS)


def extract_pages(raw: str) -> tuple[str, str] | None:
    """First page range in the entry — used by the Phase 3 metadata diff."""
    match = PAGES_RE.search(raw)
    if match:
        return match.group(1), match.group(2)
    return None


def normalise_isbn(raw: str) -> str | None:
    match = ISBN_RE.search(raw)
    if not match:
        return None
    return re.sub(r"[^\dXx]", "", match.group(0).upper()).replace("ISBN", "")
