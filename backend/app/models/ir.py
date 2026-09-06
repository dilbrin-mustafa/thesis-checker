"""Canonical Document IR — the one contract that matters (doc 01 §3.2).

Every parser (PDF/DOCX/LaTeX) produces this; every analyser consumes only
this. Analysers never touch the original file.

Non-negotiable property: every element carries a character offset range into
``plain_text`` plus a source locator, so findings can be highlighted in the
original document.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

IR_SCHEMA_VERSION = "1.0"

BlockType = Literal[
    "heading",
    "paragraph",
    "caption",
    "list_item",
    "table",
    "figure",
    "equation",
    "code",
    "footnote",
    "bibitem",
    "quote",
]

SourceFormat = Literal["docx", "pdf", "latex"]

SectionKind = Literal[
    "title_page",
    "statement",
    "abstract_pl",
    "abstract_en",
    "toc",
    "introduction",
    "literature_review",
    "methodology",
    "results",
    "summary",
    "bibliography",
    "appendix",
    "other",
]

ExcludeReason = Literal[
    "front_matter",
    "toc",
    "bibliography",
    "appendix",
    "code",
    "quote",
    "other",
]


class Span(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class Locator(BaseModel):
    """Source locator for highlighting: page+bbox (PDF), xml path (DOCX), line (LaTeX)."""

    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None  # [x0, y0, x1, y1]
    xml_path: str | None = None
    line: int | None = None


class BlockStyle(BaseModel):
    font: str | None = None
    size_pt: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    line_spacing: float | None = None
    align: str | None = None
    indent_cm: float | None = None
    space_before_pt: float | None = None


class Block(BaseModel):
    id: str
    type: BlockType
    level: int | None = Field(default=None, ge=1, le=6)
    text: str
    span: Span
    locator: Locator = Field(default_factory=Locator)
    style: BlockStyle = Field(default_factory=BlockStyle)
    flags: list[str] = Field(default_factory=list)


class Source(BaseModel):
    format: SourceFormat
    filename: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pages: int = Field(ge=0)
    words: int = Field(ge=0)


class Language(BaseModel):
    primary: str = Field(pattern=r"^[a-z]{2}$")
    per_section: dict[str, str] = Field(default_factory=dict)


class Section(BaseModel):
    id: str
    kind: SectionKind
    title: str
    block_ids: list[str]
    page_range: list[int] | None = None


class Citation(BaseModel):
    id: str
    raw: str
    style: str | None = None  # numeric | author_year | ...
    block_id: str
    span: Span
    ref_id: str | None = None


class ParsedReference(BaseModel):
    authors: list[str] = Field(default_factory=list)
    title: str | None = None
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None


class Reference(BaseModel):
    id: str
    raw: str
    parsed: ParsedReference = Field(default_factory=ParsedReference)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Figure(BaseModel):
    id: str
    number: str | None = None
    caption: str | None = None
    page: int | None = None
    referenced_by: list[str] = Field(default_factory=list)


class Table(BaseModel):
    id: str
    number: str | None = None
    caption: str | None = None
    page: int | None = None
    referenced_by: list[str] = Field(default_factory=list)


class Equation(BaseModel):
    id: str
    number: str | None = None
    page: int | None = None


class Layout(BaseModel):
    margins_cm: dict[str, float] = Field(default_factory=dict)
    page_size: str | None = None
    numbering_starts_at: int | None = None


class Exclusion(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    reason: ExcludeReason


class DocumentIR(BaseModel):
    """Frozen contract v1.0 — parsers produce it, analysers consume it."""

    schema_version: Literal["1.0"] = "1.0"
    doc_id: str
    source: Source
    language: Language
    plain_text: str
    blocks: list[Block]
    sections: list[Section] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    equations: list[Equation] = Field(default_factory=list)
    layout: Layout = Field(default_factory=Layout)
    exclusion_mask: list[Exclusion] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def is_excluded(self, start: int, end: int) -> bool:
        """True if the [start, end) range overlaps any exclusion zone."""
        return any(e.start < end and start < e.end for e in self.exclusion_mask)

    def block_text_slice(self, block: Block) -> str:
        """Return the plain_text slice a block claims — for contract checks."""
        return self.plain_text[block.span.start : block.span.end]
