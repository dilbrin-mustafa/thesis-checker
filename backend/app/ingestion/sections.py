"""Section classification and structure recovery (doc 01 §4.1 step 3).

Headings are mapped to canonical ``SectionKind`` values with a bilingual
lexicon plus positional fallback. Two design choices worth defending in the
thesis:

* **Lexicon first, position second.** PG theses are overwhelmingly regular, so
  a keyword lexicon covers nearly everything; position only rescues documents
  with idiosyncratic headings ("Część teoretyczna", "Chapter 2").
* **Sections are cut at the shallowest heading level present.** A document
  without ``\\chapter`` still gets sensible sections because ``\\section``
  becomes level 1 in the parser's own numbering.
"""

from __future__ import annotations

import re
import unicodedata

from app.models.ir import Block, Section, SectionKind

#: heading text (lowercased, de-accented, punctuation stripped) → kind.
#: Checked in order; first prefix/keyword hit wins.
LEXICON: tuple[tuple[SectionKind, tuple[str, ...]], ...] = (
    (
        "title_page",
        (
            "politechnika",
            "uniwersytet",
            "university of technology",
            "wydzial",
            "faculty of",
            "praca dyplomowa",
            "praca magisterska",
            "praca inzynierska",
            "kierunek studiow",
            "thesis title",
        ),
    ),
    (
        "statement",
        (
            "oswiadczenie",
            "declaration of authorship",
            "authors declaration",
            "swiadectwo oryginalnosci",
            "statement of originality",
        ),
    ),
    (
        "abstract_pl",
        ("streszczenie", "streszczenie pracy", "slowa kluczowe"),
    ),
    (
        "abstract_en",
        ("abstract", "summary of the thesis", "keywords"),
    ),
    (
        "toc",
        (
            "spis tresci",
            "spis zawartosci",
            "table of contents",
            "contents",
            "spis rysunkow",
            "list of figures",
            "spis tabel",
            "list of tables",
            "spis skrotow",
            "list of abbreviations",
            "wykaz skrotow",
        ),
    ),
    (
        "bibliography",
        (
            "bibliografia",
            "literatura",
            "spis literatury",
            "references",
            "bibliography",
            "works cited",
            "zrodla",
        ),
    ),
    (
        "appendix",
        (
            "dodatek",
            "dodatki",
            "zalacznik",
            "zalaczniki",
            "appendix",
            "appendices",
            "supplementary",
        ),
    ),
    (
        "introduction",
        (
            "wstep",
            "wprowadzenie",
            "introduction",
            "cel i zakres pracy",
            "cel pracy",
            "scope of the thesis",
            "problem statement",
        ),
    ),
    (
        "literature_review",
        (
            "przeglad literatury",
            "stan wiedzy",
            "stan techniki",
            "literature review",
            "related work",
            "background",
            "podstawy teoretyczne",
            "teoria",
            "czesc teoretyczna",
            "theoretical",
        ),
    ),
    (
        "methodology",
        (
            "metodyka",
            "metodologia",
            "methodology",
            "methods",
            "materialy i metody",
            "materials and methods",
            "projekt",
            "design",
            "implementacja",
            "implementation",
            "narzedzia",
            "tools",
            "srodowisko",
        ),
    ),
    (
        "results",
        (
            "wyniki",
            "results",
            "eksperymenty",
            "experiments",
            "ewaluacja",
            "evaluation",
            "badania",
            "analiza wynikow",
            "discussion",
            "dyskusja",
            "testy",
            "testing",
            "veryfikacja",
            "verification",
        ),
    ),
    (
        "summary",
        (
            "podsumowanie",
            "zakonczenie",
            "wnioski",
            "conclusion",
            "conclusions",
            "summary and conclusions",
            "concluding remarks",
            "future work",
            "kierunki dalszych prac",
        ),
    ),
)

#: Positional fallback: the i-th top-level heading, when the lexicon missed.
POSITIONAL_HINTS: tuple[tuple[int, SectionKind], ...] = ((0, "introduction"),)

_HEADING_NOISE_RE = re.compile(
    r"^[\d\.\)\s]*(?:rozdzial|chapter|czesc|part)?[\s\d\.\)]*", re.IGNORECASE
)


def _normalise(title: str) -> str:
    """Lowercase, strip accents, strip numbering ("2.1. Metodyka" → "metodyka")."""
    text = unicodedata.normalize("NFKD", title.strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s%-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    text = _HEADING_NOISE_RE.sub("", text).strip()
    return text


def classify_heading(title: str) -> SectionKind | None:
    """Map a heading title to a canonical section kind, or None."""
    normalised = _normalise(title)
    if not normalised:
        return None
    for kind, needles in LEXICON:
        for needle in needles:
            if (
                normalised == needle
                or normalised.startswith(needle + " ")
                or normalised.startswith(needle + ",")
            ):
                return kind
    # looser pass: keyword anywhere (handles "Wyniki badań eksperymentalnych")
    for kind, needles in LEXICON:
        for needle in needles:
            if needle in normalised:
                return kind
    return None


def looks_like_toc_block(text: str) -> bool:
    """ToC lines carry dot leaders or a tab/wide-gap separated page number."""
    stripped = text.strip()
    if not stripped or stripped.endswith((".", ",", ";", ":")):
        return False
    if re.search(r"\.{4,}", stripped):
        return True
    return bool(re.search(r"(?:\t| {2,})\d{1,4}$", stripped))


def build_sections(blocks: list[Block], *, source_format: str) -> list[Section]:
    """Cut the block stream into sections at the shallowest heading level.

    Blocks before the first heading become a ``title_page`` section (front
    matter); they are excluded downstream regardless of classification.
    """
    by_id = {b.id: b for b in blocks}
    headings = [b for b in blocks if b.type == "heading"]
    if not headings:
        if not blocks:
            return []
        return [
            Section(
                id="s0001",
                kind="other",
                title="(no headings detected)",
                block_ids=[b.id for b in blocks],
                page_range=_page_range(blocks),
            )
        ]

    top_level = min(b.level or 1 for b in headings)
    sections: list[Section] = []
    pending_ids: list[str] = []
    pending_blocks: list[Block] = []
    current: dict[str, object] | None = None
    seq = 0

    def flush() -> None:
        nonlocal seq, current, pending_ids, pending_blocks
        if current is None:
            return
        seq += 1
        section = Section(
            id=f"s{seq:04d}",
            kind=str(current["kind"]),  # type: ignore[arg-type]
            title=str(current["title"]),
            block_ids=[str(current["heading_id"]), *pending_ids],
            page_range=_page_range(pending_blocks),
        )
        sections.append(section)
        pending_ids, pending_blocks = [], []

    for block in blocks:
        is_cut = block.type == "heading" and (block.level or 1) <= top_level
        if is_cut:
            flush()
            kind = classify_heading(block.text) or "other"
            current = {"kind": kind, "title": block.text.strip(), "heading_id": block.id}
            continue
        pending_ids.append(block.id)
        pending_blocks.append(block)

    if current is None:
        # front matter only
        seq += 1
        sections.append(
            Section(
                id=f"s{seq:04d}",
                kind="title_page",
                title="(front matter)",
                block_ids=pending_ids,
                page_range=_page_range(pending_blocks),
            )
        )
        return sections

    flush()

    # Front matter (title page, statement, abstracts) precedes the first cut.
    first_heading_index = next(
        (i for i, b in enumerate(blocks) if b.id == sections[0].block_ids[0]), 0
    )
    leading = blocks[:first_heading_index]
    if leading:
        seq += 1
        sections.insert(
            0,
            Section(
                id=f"s{seq:04d}",
                kind="title_page",
                title="(front matter)",
                block_ids=[b.id for b in leading],
                page_range=_page_range(leading),
            ),
        )
    _renumber(sections)
    _promote_title_page(sections, by_id)
    _apply_positional_fallback(sections, source_format=source_format)
    return sections


def _renumber(sections: list[Section]) -> None:
    for index, section in enumerate(sections, start=1):
        section.id = f"s{index:04d}"


#: kinds that only ever appear in or after the front matter
FRONT_MATTER_KINDS = frozenset(
    {"statement", "abstract_pl", "abstract_en", "toc", "introduction", "bibliography"}
)


def _promote_title_page(sections: list[Section], blocks_by_id: dict[str, Block]) -> None:
    """Label a leading unclassified section as the title page.

    Title pages are the one part of a thesis nobody formats as a numbered
    heading, so the lexicon alone misses them. Two signals rescue it: the
    parser flagged a block with the Title style, or the section sits in front
    of unmistakable front matter (statement / abstracts / ToC).
    """
    if not sections or sections[0].kind != "other":
        return
    first = sections[0]
    heading = blocks_by_id.get(first.block_ids[0]) if first.block_ids else None
    titled = heading is not None and "title" in heading.flags
    front_matter_follows = any(s.kind in FRONT_MATTER_KINDS for s in sections[1:])
    if titled or front_matter_follows:
        first.kind = "title_page"


def _apply_positional_fallback(sections: list[Section], *, source_format: str) -> None:
    """Label the first unclassified body section as the introduction.

    Only applies when the document has an unlabelled first section and no
    section was already classified as an introduction.
    """
    del source_format  # reserved: format-specific hints if we ever need them
    if any(s.kind == "introduction" for s in sections):
        return
    for index, section in enumerate(sections):
        if section.kind == "title_page":
            continue
        if index in {i for i, _ in POSITIONAL_HINTS} and section.kind == "other":
            section.kind = "introduction"
        break


def _page_range(blocks: list[Block]) -> list[int] | None:
    pages = [b.locator.page for b in blocks if b.locator.page is not None]
    if not pages:
        return None
    return [min(pages), max(pages)]
