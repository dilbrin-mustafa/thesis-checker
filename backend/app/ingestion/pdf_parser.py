"""PDF → DocumentIR (Phase 1, week 6 — deliberately timeboxed).

PDF is the lowest-fidelity source: geometry is explicit but *logical structure
is not present in the file at all* and must be inferred. Two techniques carry
most of the accuracy, and both are implemented here:

1. **The embedded table of contents is ground truth.** When ``doc.get_toc()``
   returns entries, their titles and levels are matched back onto text blocks
   by page, and those matches override anything the font-size heuristic says.
2. **Font-size clustering** induces levels for the headings the ToC misses
   (unnumbered headings, subsubsections, documents with no ToC at all).

Everything else — captions, floats, tables, equations — is a heuristic with a
stated confidence, and the parser records *how* it decided in
``extra["pdf"]["heading_source"]`` so the accuracy report can separate
ToC-validated results from inferred ones. Per doc 05 Phase 1, this module is
one week of effort and is explicitly allowed to be imperfect — but it must be
*measured*, which is what ``research/results/ingestion.md`` is for.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.ingestion.base import BaseParser
from app.ingestion.builder import IRBuilder, file_sha256, normalise_ws
from app.ingestion.guards import check_file_size, check_pdf
from app.models.ir import (
    BlockStyle,
    BlockType,
    DocumentIR,
    Equation,
    Figure,
    Layout,
    Locator,
    Table,
)

PT_PER_CM = 28.3465
CAPTION_RE = re.compile(
    r"^\s*(?:(?P<fig>Rys\.|Rysunek|Figure|Fig\.|Wykres|Schemat|Fot\.|Fotografia)"
    r"|(?P<tab>Tab\.|Tabela|Table|Tablica))\s*\.?\s*(?P<num>[\dIVXL]+(?:[.\-][\dIVXL]+)*)",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*(?:[•·◦‣⁃▪–—-]|\d{1,3}[.)]|[a-zA-Z][.)])\s+")
MATH_FONT_HINTS = ("math", "cmmi", "cmsy", "cmex", "msam", "msbm", "rsfs", "euscript", "symbol")
PAGE_SIZES: tuple[tuple[str, float, float], ...] = (
    ("A4", 21.0, 29.7),
    ("A5", 14.8, 21.0),
    ("Letter", 21.59, 27.94),
    ("B5", 17.6, 25.0),
)
#: A heading is at most this many characters; longer "big text" is a title line.
MAX_HEADING_CHARS = 140


class PdfParser(BaseParser):
    """PyMuPDF span extraction + structure induction."""

    format = "pdf"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def parse(self, path: Path, doc_id: str, filename: str) -> DocumentIR:
        import pymupdf

        if hasattr(pymupdf, "no_recommend_layout"):
            # pymupdf>=1.28 advertises its optional layout package on the first
            # parse; a batch run over a corpus should not print that 20 times.
            pymupdf.no_recommend_layout()

        check_file_size(path, self.settings)
        page_count = check_pdf(path, self.settings)

        builder = IRBuilder(
            doc_id=doc_id,
            source_format="pdf",
            filename=filename,
            sha256=file_sha256(path),
        )
        warnings: list[str] = []
        raw_blocks: list[_RawBlock] = []
        figure_boxes: list[tuple[int, tuple[float, float, float, float]]] = []
        text_bounds: list[tuple[float, float, float, float]] = []

        with pymupdf.open(path) as doc:
            toc = _safe_toc(doc, warnings)
            layout, page_dims = _page_layout(doc)
            limit = min(page_count, self.settings.max_pages)
            for page_index in range(limit):
                page = doc.load_page(page_index)
                page_no = page_index + 1
                table_regions = _table_regions(page, warnings)
                for block in _iter_text_blocks(page, page_no):
                    if _inside_any(block.bbox, table_regions):
                        continue
                    raw_blocks.append(block)
                    text_bounds.append(block.bbox)
                for image_bbox in _iter_image_boxes(page, page_no):
                    figure_boxes.append((page_no, image_bbox))
                for region in table_regions:
                    raw_blocks.append(region.block)

        raw_blocks.sort(key=lambda b: (b.page, b.bbox[1], b.bbox[0]))

        body_size, size_levels = _size_hierarchy(raw_blocks)
        toc_matches = _match_toc(raw_blocks, toc)
        calibrate_levels(size_levels, raw_blocks, toc_matches)
        heading_source = (
            "toc" if toc_matches else ("font-size clustering" if size_levels else "none")
        )
        if toc_matches and size_levels:
            heading_source = "toc+font-size"

        for raw in raw_blocks:
            level = toc_matches.get(id(raw))
            block_type, level, flags = _classify(raw, body_size, size_levels, level)
            builder.add(
                type=block_type,
                text=raw.text,
                level=level,
                locator=Locator(page=raw.page, bbox=[round(v, 2) for v in raw.bbox]),
                style=BlockStyle(
                    font=raw.font,
                    size_pt=round(raw.size, 1) if raw.size else None,
                    bold=raw.bold or None,
                    italic=raw.italic or None,
                ),
                flags=flags,
            )

        ir = builder.build(
            pages=page_count,
            layout=layout,
            extra={
                "pdf": {
                    "heading_source": heading_source,
                    "body_size_pt": round(body_size, 1) if body_size else None,
                    "toc_entries": len(toc),
                    "text_coverage": round(_coverage(raw_blocks, page_count), 3),
                },
                "parse_warnings": warnings,
            },
        )
        ir.figures, ir.tables, ir.equations = _collect_floats(builder.blocks, figure_boxes)
        ir.layout.margins_cm = _margins(text_bounds, page_dims)
        return ir


# -- raw extraction -----------------------------------------------------------
class _RawBlock:
    __slots__ = ("bbox", "bold", "font", "italic", "math_ratio", "page", "size", "text")

    def __init__(
        self,
        *,
        page: int,
        bbox: tuple[float, float, float, float],
        text: str,
        font: str | None,
        size: float,
        bold: bool,
        italic: bool,
        math_ratio: float = 0.0,
    ) -> None:
        self.page = page
        self.bbox = bbox
        self.text = text
        self.font = font
        self.size = size
        self.bold = bold
        self.italic = italic
        self.math_ratio = math_ratio


def _iter_text_blocks(page: Any, page_no: int) -> list[_RawBlock]:
    out: list[_RawBlock] = []
    try:
        data = page.get_text("dict", sort=True)
    except Exception:  # pragma: no cover - pymupdf raises on damaged pages
        return out
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        lines = block.get("lines", [])
        if not lines:
            continue
        text = _join_lines(lines)
        if not text:
            continue
        font, size, bold, italic, math_ratio = _dominant_style(lines)
        out.append(
            _RawBlock(
                page=page_no,
                bbox=tuple(block["bbox"]),
                text=text,
                font=font,
                size=size,
                bold=bold,
                italic=italic,
                math_ratio=math_ratio,
            )
        )
    return out


#: PDF fonts encode ligatures as single code points, so "bibliogra\ufb01a" fails
#: every downstream string match. NFKC would fix that too, but it also rewrites
#: superscripts and fractions, so only the typographic ligatures are expanded.
LIGATURES: dict[str, str] = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}


def _expand_ligatures(text: str) -> str:
    for ligature, expansion in LIGATURES.items():
        if ligature in text:
            text = text.replace(ligature, expansion)
    return text


def _join_lines(lines: list[dict[str, Any]]) -> str:
    """Reflow a block's lines into one paragraph string.

    Handles the two things that break naive extraction: soft hyphenation at
    line ends (``anal-\\nityczna`` → ``analityczna``) and ligature artefacts.
    """
    pieces: list[str] = []
    for line in lines:
        spans = line.get("spans", [])
        line_text = "".join(span.get("text", "") for span in spans)
        line_text = line_text.replace("\xad", "")
        if not line_text:
            continue
        if pieces:
            previous = pieces[-1]
            if previous.endswith("-") and line_text[:1].islower():
                pieces[-1] = previous[:-1]
                pieces.append(line_text)
                continue
            pieces.append(" ")
        pieces.append(line_text)
    return normalise_ws(_expand_ligatures("".join(pieces)))


def _dominant_style(lines: list[dict[str, Any]]) -> tuple[str | None, float, bool, bool, float]:
    """Character-weighted dominant font/size/bold, plus a math-font ratio."""
    font_weight: Counter[str] = Counter()
    size_weight: Counter[float] = Counter()
    bold_chars = 0
    italic_chars = 0
    math_chars = 0
    total = 0
    for line in lines:
        for span in line.get("spans", []):
            text = span.get("text", "")
            weight = len(text)
            if not weight:
                continue
            total += weight
            font = str(span.get("font", ""))
            font_weight[font] += weight
            size_weight[round(float(span.get("size", 0.0)), 1)] += weight
            flags = int(span.get("flags", 0))
            if flags & 16:
                bold_chars += weight
            if flags & 2:
                italic_chars += weight
            if any(hint in font.lower() for hint in MATH_FONT_HINTS):
                math_chars += weight
    dominant_font: str | None = font_weight.most_common(1)[0][0] if font_weight else None
    size = size_weight.most_common(1)[0][0] if size_weight else 0.0
    return (
        dominant_font,
        size,
        total > 0 and bold_chars / total > 0.5,
        total > 0 and italic_chars / total > 0.5,
        math_chars / total if total else 0.0,
    )


def _iter_image_boxes(page: Any, page_no: int) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    try:
        data = page.get_text("dict")
    except Exception:  # pragma: no cover
        return boxes
    for block in data.get("blocks", []):
        if block.get("type", 0) == 1:
            boxes.append(tuple(block["bbox"]))
    del page_no
    return boxes


def _table_regions(page: Any, warnings: list[str]) -> list[_TableRegion]:
    try:
        finder = page.find_tables()
    except Exception as exc:  # damaged page; tables are best-effort
        warnings.append(f"table detection failed on page {page.number + 1}: {exc}")
        return []
    regions: list[_TableRegion] = []
    for table in getattr(finder, "tables", []):
        try:
            rows = table.extract()
        except Exception:
            continue
        text = "\n".join(
            "\t".join((cell or "").strip() for cell in row) for row in rows if any(row)
        )
        text = text.strip()
        if not text:
            continue
        bbox = tuple(table.bbox)
        regions.append(
            _TableRegion(
                bbox=bbox,
                block=_RawBlock(
                    page=int(page.number) + 1,
                    bbox=bbox,
                    text=text,
                    font=None,
                    size=0.0,
                    bold=False,
                    italic=False,
                ),
            )
        )
    return regions


class _TableRegion:
    __slots__ = ("bbox", "block")

    def __init__(self, *, bbox: tuple[float, float, float, float], block: _RawBlock) -> None:
        self.bbox = bbox
        self.block = block


def _inside_any(
    bbox: tuple[float, float, float, float],
    regions: list[_TableRegion],
    threshold: float = 0.5,
) -> bool:
    """True when a text block is mostly inside a detected table."""
    return any(_overlap_ratio(bbox, region.bbox) >= threshold for region in regions)


def _overlap_ratio(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = width * height
    own = max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)
    return area / own


# -- structure induction ------------------------------------------------------
def _size_hierarchy(raw_blocks: list[_RawBlock]) -> tuple[float, dict[float, int]]:
    """Body size = most frequent size; larger sizes map to heading levels."""
    weights: Counter[float] = Counter()
    for block in raw_blocks:
        if block.size:
            weights[round(block.size, 1)] += len(block.text)
    if not weights:
        return 0.0, {}
    body_size = weights.most_common(1)[0][0]
    larger = sorted((s for s in weights if s > body_size * 1.05), reverse=True)
    levels = {size: min(index + 1, 6) for index, size in enumerate(larger)}
    return body_size, levels


def _match_toc(raw_blocks: list[_RawBlock], toc: list[list[Any]]) -> dict[int, int]:
    """Map ToC entries onto text blocks. Returns ``id(block) → level``."""
    matches: dict[int, int] = {}
    if not toc:
        return matches
    by_page: dict[int, list[_RawBlock]] = {}
    for block in raw_blocks:
        by_page.setdefault(block.page, []).append(block)

    for entry in toc:
        if len(entry) < 3:
            continue
        level, title, page = int(entry[0]), str(entry[1]), int(entry[2])
        needle = normalise_ws(title).lower()
        if not needle:
            continue
        candidates = [*by_page.get(page, []), *by_page.get(page + 1, [])]
        best: _RawBlock | None = None
        best_score = 0.0
        for block in candidates:
            haystack = block.text.lower()
            if needle in haystack:
                score = len(needle) / max(len(haystack), 1)
            elif _prefix_score(needle, haystack) > 0.8:
                score = _prefix_score(needle, haystack) * 0.9
            else:
                continue
            if score > best_score:
                best, best_score = block, score
        if best is not None:
            matches[id(best)] = max(1, min(level, 6))
    return matches


def _prefix_score(needle: str, haystack: str) -> float:
    """How much of *needle* the block starts with (PDFs mangle accents/numbers)."""
    if not needle:
        return 0.0
    words = needle.split()
    matched = 0
    haystack_words = haystack.split()
    for word in words:
        if matched < len(haystack_words) and haystack_words[matched].startswith(word[:4]):
            matched += 1
        else:
            break
    return matched / len(words)


def calibrate_levels(
    size_levels: dict[float, int],
    raw_blocks: list[_RawBlock],
    toc_matches: dict[int, int],
) -> None:
    """Validate the font-size clustering against the embedded ToC.

    The ToC tells us the *true* level of the headings it lists. Where the
    size-derived level disagrees, the ToC wins — and the corrected size→level
    mapping is then applied to the headings the ToC does not list (unnumbered
    headings, subsubsections), which is the highest-leverage trick available
    for PDF structure recovery (doc 05, week 6).
    """
    if not toc_matches:
        return
    votes: dict[float, Counter[int]] = {}
    for block in raw_blocks:
        level = toc_matches.get(id(block))
        if level is None or not block.size:
            continue
        votes.setdefault(round(block.size, 1), Counter())[level] += 1
    for size, counter in votes.items():
        size_levels[size] = counter.most_common(1)[0][0]


def _classify(
    raw: _RawBlock,
    body_size: float,
    size_levels: dict[float, int],
    toc_level: int | None,
) -> tuple[BlockType, int | None, list[str]]:
    """Decide block type, heading level and flags for one raw PDF block."""
    flags: list[str] = []
    size = round(raw.size, 1)

    if raw.text and CAPTION_RE.match(raw.text):
        return "caption", None, flags
    if raw.math_ratio > 0.5 and len(raw.text) < 200:
        return "equation", None, ["math_fonts"]
    if size and size_levels and size in size_levels and _looks_like_heading(raw, body_size):
        level = toc_level if toc_level is not None else size_levels[size]
        if toc_level is None:
            flags.append("heading_inferred")
        return "heading", level, flags
    if toc_level is not None:
        return "heading", toc_level, flags
    if BULLET_RE.match(raw.text):
        return "list_item", None, flags
    if "\t" in raw.text and raw.font is None:
        return "table", None, ["table_detected"]
    return "paragraph", None, flags


def _looks_like_heading(raw: _RawBlock, body_size: float) -> bool:
    if len(raw.text) > MAX_HEADING_CHARS:
        return False
    if raw.text.endswith((".", ",", ";", ":")):
        return False
    if raw.size >= body_size * 1.05:
        return True
    return bool(raw.bold) and raw.size >= body_size * 0.95


# -- floats -------------------------------------------------------------------
def _collect_floats(
    blocks: list[Any], figure_boxes: list[tuple[int, tuple[float, float, float, float]]]
) -> tuple[list[Figure], list[Table], list[Equation]]:
    """Caption-first float register (same rule as the DOCX parser).

    Captions carry the number, so they win; a detected table region only
    becomes a new entry when no caption on the same page already claimed it.
    """
    figures: list[Figure] = []
    tables: list[Table] = []
    equations: list[Equation] = []
    consumed: set[int] = set()

    for position, block in enumerate(blocks):
        if block.type == "equation":
            equations.append(
                Equation(
                    id=f"e{len(equations) + 1:03d}",
                    number=_trailing_number(block.text),
                    page=block.locator.page,
                )
            )
            continue
        if block.type != "caption":
            continue
        match = CAPTION_RE.match(block.text)
        if not match:
            continue
        consumed.add(position)
        if match.group("fig"):
            figures.append(
                Figure(
                    id=f"f{len(figures) + 1:03d}",
                    number=match.group("num"),
                    caption=block.text,
                    page=block.locator.page,
                )
            )
        else:
            tables.append(
                Table(
                    id=f"t{len(tables) + 1:03d}",
                    number=match.group("num"),
                    caption=block.text,
                    page=block.locator.page,
                )
            )

    for position, block in enumerate(blocks):
        if block.type not in ("table", "figure"):
            continue
        caption_index = _neighbour_caption(blocks, position)
        if caption_index is not None and caption_index in consumed:
            continue
        number = caption = None
        if caption_index is not None:
            match = CAPTION_RE.match(blocks[caption_index].text)
            number = match.group("num") if match else None
            caption = blocks[caption_index].text
            consumed.add(caption_index)
        if block.type == "table":
            tables.append(
                Table(
                    id=f"t{len(tables) + 1:03d}",
                    number=number,
                    caption=caption,
                    page=block.locator.page,
                )
            )
        else:
            figures.append(
                Figure(
                    id=f"f{len(figures) + 1:03d}",
                    number=number,
                    caption=caption,
                    page=block.locator.page,
                )
            )

    # uncaptioned raster images: register them so "figure without a caption"
    # and "figure never referenced in the text" remain detectable findings.
    for page_no, _bbox in figure_boxes:
        if _page_has_captioned_figure(figures, page_no):
            continue
        figures.append(
            Figure(id=f"f{len(figures) + 1:03d}", number=None, caption=None, page=page_no)
        )
    return figures, tables, equations


def _neighbour_caption(blocks: list[Any], position: int) -> int | None:
    """Index of a numbered caption within three blocks (above or below)."""
    for offset in (-1, 1, -2, 2, -3, 3):
        index = position + offset
        if 0 <= index < len(blocks):
            candidate = blocks[index]
            if candidate.type == "caption" and CAPTION_RE.match(candidate.text):
                return index
    return None


def _page_has_captioned_figure(figures: list[Figure], page_no: int) -> bool:
    return any(figure.page == page_no and figure.caption for figure in figures)


def _trailing_number(text: str) -> str | None:
    match = re.search(r"[\(\[]\s*([\d]+(?:\.[\d]+)*)\s*[\)\]]\s*$", text.strip())
    return match.group(1) if match else None


# -- layout -------------------------------------------------------------------
def _page_layout(doc: Any) -> tuple[Layout, tuple[float, float]]:
    """Page size name plus (width_cm, height_cm) of the first page."""
    try:
        rect = doc.load_page(0).rect
    except Exception:  # pragma: no cover - unreadable first page
        return Layout(), (21.0, 29.7)
    width_cm = float(rect.width) / PT_PER_CM
    height_cm = float(rect.height) / PT_PER_CM
    for name, width, height in PAGE_SIZES:
        if abs(width_cm - width) <= 0.4 and abs(height_cm - height) <= 0.4:
            return Layout(page_size=name), (width, height)
    label = f"{width_cm:.1f}x{height_cm:.1f}cm"
    return Layout(page_size=label), (width_cm, height_cm)


def _margins(
    bounds: list[tuple[float, float, float, float]], page_dims: tuple[float, float]
) -> dict[str, float]:
    """Margins inferred from where text actually sits on the page.

    This is an *estimate* (the ink extent, not the declared margin) and is
    reported as such; DOCX gives the declared values exactly.
    """
    if not bounds:
        return {}
    width_cm, height_cm = page_dims
    left = min(b[0] for b in bounds)
    top = min(b[1] for b in bounds)
    right = max(b[2] for b in bounds)
    bottom = max(b[3] for b in bounds)
    return {
        "inner": round(max(left, 0.0) / PT_PER_CM, 2),
        "top": round(max(top, 0.0) / PT_PER_CM, 2),
        "outer": round(max(width_cm - right / PT_PER_CM, 0.0), 2),
        "bottom": round(max(height_cm - bottom / PT_PER_CM, 0.0), 2),
    }


def _coverage(blocks: list[_RawBlock], pages: int) -> float:
    """Mean extracted characters per page, normalised — a parsing-quality proxy."""
    if pages <= 0:
        return 0.0
    total_chars = sum(len(b.text) for b in blocks)
    return min(total_chars / (pages * 1500), 1.0)


def _safe_toc(doc: Any, warnings: list[str]) -> list[list[Any]]:
    try:
        toc = doc.get_toc(simple=True)
    except Exception as exc:
        warnings.append(f"ToC extraction failed: {exc}")
        return []
    return [list(entry) for entry in toc or []]
