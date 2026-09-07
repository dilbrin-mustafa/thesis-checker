"""DOCX → DocumentIR (Phase 1, week 4).

DOCX is the highest-fidelity source: real font, size, spacing and indent data
come straight out of the package, which makes it the ground truth for the
format rules in Phase 2.

Two deliberate implementation choices:

* **All XML is parsed with ``defusedxml``** (doc 02 §3 — XXE, billion laughs).
  We read the OOXML parts ourselves instead of going through ``python-docx``'s
  lxml parser, so the hardening requirement is structural rather than a matter
  of trusting a dependency's configuration. ``python-docx`` stays a dev/test
  dependency for building fixtures.
* **Formatting is resolved through the full inheritance chain**
  ``docDefaults → basedOn* → style → direct``, because a thesis template puts
  almost everything in styles and almost nothing in direct formatting. A parser
  that only read direct ``rPr`` would report "no font" for every paragraph.

Page numbers are *not* recoverable from DOCX (they are a rendering property),
so ``locator.page`` stays ``None`` and ``source.pages`` comes from
``docProps/app.xml`` when the authoring application wrote it.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring as safe_fromstring

from app.core.config import Settings, get_settings
from app.ingestion.base import BaseParser
from app.ingestion.builder import IRBuilder, file_sha256, normalise_ws
from app.ingestion.errors import ParseError
from app.ingestion.guards import check_docx_container, check_file_size
from app.models.ir import (
    Block,
    BlockStyle,
    DocumentIR,
    Equation,
    Figure,
    Layout,
    Locator,
    Table,
)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "v": "urn:schemas-microsoft-com:vml",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
}


def qn(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


# -- style-name lexicons (Word writes these in the document's own language) ----
HEADING_STYLE_RE = re.compile(r"^(?:heading|nag[łl][óo]wek)\s*([1-9])$", re.IGNORECASE)
HEADING_STYLE_ID_RE = re.compile(r"^(?:heading|naglowek|nagłówek)\s*([1-9])$", re.IGNORECASE)
CAPTION_NAMES = frozenset(
    {"caption", "caption text", "legenda", "tekst legendy", "napis", "podpis"}
)
QUOTE_NAMES = frozenset(
    {
        "quote",
        "cytat",
        "intense quote",
        "cytat intensywny",
        "block text",
        "blok tekstowy",
        "block quote",
        "block quotation",
    }
)
LIST_NAMES = frozenset(
    {
        "list paragraph",
        "akapit listy",
        "list bullet",
        "list number",
        "lista punktowana",
        "lista numerowana",
        "list continue",
    }
)
CODE_HINTS = ("code", "kod", "listing", "verbatim", "preformatted", "html", "listing")
TOC_HINTS = ("toc", "spis treści", "spis tresci", "table of contents", "contents")
TITLE_NAMES = frozenset({"title", "tytuł", "tytul"})
SUBTITLE_NAMES = frozenset({"subtitle", "podtytuł", "podtytul"})
MONOSPACE_FONTS = frozenset(
    {
        "courier",
        "courier new",
        "consolas",
        "menlo",
        "monaco",
        "lucida console",
        "dejavu sans mono",
        "liberation mono",
        "andale mono",
        "inconsolata",
        "fira code",
        "source code pro",
    }
)
CAPTION_TEXT_RE = re.compile(
    r"^\s*(?:(?P<fig>Rys\.|Rysunek|Figure|Fig\.|Wykres|Schemat|Fot\.|Fotografia)"
    r"|(?P<tab>Tab\.|Tabela|Table|Tablica))\s*\.?\s*(?P<num>[\dIVXL]+(?:[.\-][\dIVXL]+)*)",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*(?:[•·◦‣⁃–—-]|\d{1,3}[.)]|[a-zA-Z][.)])\s+")

TWIPS_PER_CM = 567.0


@dataclass
class StyleEntry:
    style_id: str
    name: str
    based_on: str | None
    kind: str
    props: dict[str, Any] = field(default_factory=dict)


def _bool(value: str | None) -> bool | None:
    if value is None:
        return True
    return value.lower() not in ("0", "false", "none", "off")


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _child(el: Element | None, tag: str) -> Element | None:
    return el.find(qn(tag)) if el is not None else None


def _val(el: Element | None, attr: str = "val") -> str | None:
    """Read a ``w:``-namespaced attribute (OOXML qualifies attribute names)."""
    if el is None:
        return None
    return el.get(qn(f"w:{attr}"))


def _text_of(el: Element | None) -> str:
    return (el.text or "") if el is not None else ""


# -- formatting extraction ----------------------------------------------------
def _run_props(
    rpr: Element | None, into: dict[str, Any], theme_fonts: dict[str, str] | None = None
) -> None:
    if rpr is None:
        return
    fonts = _child(rpr, "w:rFonts")
    if fonts is not None:
        font = fonts.get(qn("w:ascii")) or fonts.get(qn("w:hAnsi")) or fonts.get(qn("w:cs"))
        if not font and theme_fonts:
            # Templates usually reference the theme ("minorHAnsi") instead of
            # naming a font; resolve it through theme1.xml or we report "none".
            font = theme_fonts.get(fonts.get(qn("w:asciiTheme")) or "") or theme_fonts.get(
                fonts.get(qn("w:hAnsiTheme")) or ""
            )
        if font:
            into["font"] = font
    size = _val(_child(rpr, "w:sz"))
    if size is not None:
        parsed = _int(size)
        if parsed:
            into["size_pt"] = parsed / 2
    bold = _child(rpr, "w:b")
    if bold is not None:
        into["bold"] = _bool(bold.get(qn("w:val")))
    italic = _child(rpr, "w:i")
    if italic is not None:
        into["italic"] = _bool(italic.get(qn("w:val")))


def _paragraph_props(ppr: Element | None, into: dict[str, Any]) -> None:
    if ppr is None:
        return
    jc = _val(_child(ppr, "w:jc"))
    if jc:
        into["align"] = {"both": "justify", "start": "left", "end": "right"}.get(jc, jc)
    spacing = _child(ppr, "w:spacing")
    if spacing is not None:
        before = _int(spacing.get(qn("w:before")))
        if before is not None:
            into["space_before_pt"] = round(before / 20, 2)
        line = _int(spacing.get(qn("w:line")))
        rule = (spacing.get(qn("w:lineRule")) or "auto").lower()
        if line is not None and rule in ("auto", "multiple"):
            # w:line=360 with lineRule=auto means 360/240 = 1.5 line spacing.
            into["line_spacing"] = round(line / 240, 2)
    indent = _child(ppr, "w:ind")
    if indent is not None:
        first_line = _int(indent.get(qn("w:firstLine")))
        if first_line is None:
            chars = _int(indent.get(qn("w:firstLineChars")))
            # firstLineChars is in 1/100 of a character; 100 ≈ one 0.5 cm indent.
            first_line = int(chars * 5.67) if chars else None
        if first_line is not None:
            into["indent_cm"] = round(first_line / TWIPS_PER_CM, 2)


class StyleTable:
    """styles.xml + docDefaults, with basedOn inheritance resolved lazily."""

    def __init__(self, styles_xml: bytes | None, theme_xml: bytes | None = None) -> None:
        self.entries: dict[str, StyleEntry] = {}
        self.defaults: dict[str, Any] = {}
        self.default_paragraph_style: str | None = None
        self.theme_fonts: dict[str, str] = _theme_fonts(theme_xml)
        if styles_xml is None:
            return
        try:
            root = safe_fromstring(styles_xml, forbid_dtd=True)
        except Exception as exc:
            raise ParseError(f"unreadable styles.xml: {exc}") from exc

        defaults_el = _child(root, "w:docDefaults")
        if defaults_el is not None:
            rpr_default = _child(_child(defaults_el, "w:rPrDefault"), "w:rPr")
            ppr_default = _child(_child(defaults_el, "w:pPrDefault"), "w:pPr")
            _run_props(rpr_default, self.defaults, self.theme_fonts)
            _paragraph_props(ppr_default, self.defaults)

        for style_el in root.findall(qn("w:style")):
            style_id = style_el.get(qn("w:styleId"))
            if not style_id:
                continue
            is_default = (style_el.get(qn("w:default")) or "").lower() in ("1", "true")
            if is_default and (style_el.get(qn("w:type")) or "paragraph") == "paragraph":
                self.default_paragraph_style = style_id
            # <w:name w:val="List Paragraph"/> — the name lives in an attribute.
            # Reading element text here silently degraded every style-name rule
            # to the styleId, which breaks non-English templates outright.
            name = (_val(_child(style_el, "w:name")) or style_id).strip()
            props: dict[str, Any] = {}
            _run_props(_child(style_el, "w:rPr"), props, self.theme_fonts)
            _paragraph_props(_child(style_el, "w:pPr"), props)
            outline = _val(_child(_child(style_el, "w:pPr"), "w:outlineLvl"))
            if outline is not None:
                level = _int(outline)
                if level is not None and 0 <= level <= 5:
                    props["outline_level"] = level + 1
            self.entries[style_id] = StyleEntry(
                style_id=style_id,
                name=name,
                based_on=_val(_child(style_el, "w:basedOn")),
                kind=style_el.get(qn("w:type")) or "paragraph",
                props=props,
            )

    def resolve(self, style_id: str | None) -> dict[str, Any]:
        """docDefaults → basedOn chain → the style itself.

        A paragraph with no explicit ``w:pStyle`` inherits the *default*
        paragraph style (``w:default="1"``, normally "Normal") — not just the
        document defaults. Missing this makes every body paragraph in a
        template-driven thesis report "no font, no spacing".
        """
        merged: dict[str, Any] = dict(self.defaults)
        seen: set[str] = set()
        current = style_id or self.default_paragraph_style
        chain: list[StyleEntry] = []
        while current and current in self.entries and current not in seen:
            seen.add(current)
            entry = self.entries[current]
            chain.append(entry)
            current = entry.based_on
        for entry in reversed(chain):
            merged.update(entry.props)
        return merged

    def name_of(self, style_id: str | None) -> str:
        if not style_id:
            style_id = self.default_paragraph_style
        if not style_id:
            return ""
        return self.entries[style_id].name.lower() if style_id in self.entries else ""


class DocxParser(BaseParser):
    """OOXML → DocumentIR. Tolerant by design: record what you can, flag the rest."""

    format = "docx"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # -- entry point -----------------------------------------------------
    def parse(self, path: Path, doc_id: str, filename: str) -> DocumentIR:
        check_file_size(path, self.settings)
        check_docx_container(path, self.settings)

        try:
            with zipfile.ZipFile(path) as zf:
                document_xml = zf.read("word/document.xml")
                styles_xml = _read_optional(zf, "word/styles.xml")
                footnotes_xml = _read_optional(zf, "word/footnotes.xml")
                app_xml = _read_optional(zf, "docProps/app.xml")
                theme_xml = _read_optional(zf, "word/theme/theme1.xml")
        except KeyError as exc:
            raise ParseError(f"missing part: {exc}") from exc
        except Exception as exc:
            raise ParseError(f"unreadable container: {exc}") from exc

        try:
            body_root = safe_fromstring(document_xml, forbid_dtd=True)
        except Exception as exc:
            raise ParseError(f"unreadable document.xml: {exc}") from exc

        styles = StyleTable(styles_xml, theme_xml)
        builder = IRBuilder(
            doc_id=doc_id,
            source_format="docx",
            filename=filename,
            sha256=file_sha256(path),
        )
        warnings: list[str] = []

        body = _child(body_root, "w:body")
        if body is None:
            raise ParseError("document.xml has no w:body")

        body_size = self._body_size(body, styles)
        for index, child in enumerate(body, start=1):
            if child.tag == qn("w:p"):
                self._add_paragraph(builder, child, index, styles, warnings, body_size)
            elif child.tag == qn("w:tbl"):
                self._add_table(builder, child, index, styles)
        self._assign_inferred_heading_levels(builder)

        self._add_footnotes(builder, footnotes_xml, warnings)

        floats = self._collect_floats(builder.blocks)
        layout = self._layout(body)
        ir = builder.build(
            pages=_page_count(app_xml),
            layout=layout,
            extra={
                "docx": {
                    "styles_used": len(styles.entries),
                    "footnotes": sum(1 for b in builder.blocks if b.type == "footnote"),
                },
                "parse_warnings": warnings,
            },
        )
        ir.figures = floats[0]
        ir.tables = floats[1]
        ir.equations = floats[2]
        return ir

    # -- blocks ----------------------------------------------------------
    def _add_paragraph(
        self,
        builder: IRBuilder,
        el: Element,
        index: int,
        styles: StyleTable,
        warnings: list[str],
        body_size: float,
    ) -> None:
        ppr = _child(el, "w:pPr")
        style_id = _val(_child(ppr, "w:pStyle"))
        style_name = styles.name_of(style_id)
        resolved = styles.resolve(style_id)
        _paragraph_props(ppr, resolved)

        first_run_props = self._first_run_props(el, styles.theme_fonts)
        run_style = styles.resolve(style_id)
        run_style.update(first_run_props)

        style = BlockStyle(
            font=run_style.get("font"),
            size_pt=run_style.get("size_pt"),
            bold=run_style.get("bold"),
            italic=run_style.get("italic"),
            line_spacing=resolved.get("line_spacing"),
            align=resolved.get("align"),
            indent_cm=resolved.get("indent_cm"),
            space_before_pt=resolved.get("space_before_pt"),
        )

        text = normalise_ws(_paragraph_text(el))
        has_image = _has_element(el, ("w:drawing", "w:pict", "w:object"))
        has_math = _has_element(el, ("m:oMath", "m:oMathPara"))
        flags: list[str] = []
        block_type: str = "paragraph"
        level: int | None = None

        if has_math:
            block_type = "equation"
            text = text or normalise_ws(_math_text(el))
        elif not text and has_image:
            block_type = "figure"
            flags.append("image_only")
        elif self._is_heading(style_id, style_name, resolved):
            block_type = "heading"
            level = self._heading_level(style_id, style_name, resolved)
            if style_name in TITLE_NAMES:
                flags.append("title")
        elif style_name in CAPTION_NAMES or CAPTION_TEXT_RE.match(text):
            block_type = "caption"
        elif any(hint in style_name for hint in TOC_HINTS):
            flags.append("toc")
        elif style_name in QUOTE_NAMES:
            block_type = "quote"
        elif self._is_list(el, style_name, text):
            block_type = "list_item"
        elif self._is_code(style_name, style):
            block_type = "code"
        elif style_name in SUBTITLE_NAMES:
            block_type = "heading"
            level = 2

        if has_image and block_type not in ("figure",):
            flags.append("contains_image")

        if not text and block_type not in ("figure", "table") and not has_image and not has_math:
            return  # empty paragraph: carries no analysable content

        if block_type == "heading" and level is None:
            level = 1
        elif block_type == "paragraph" and _looks_like_manual_heading(text, style, body_size):
            # Templates are not universal: plenty of theses format headings by
            # hand (bold + larger) with no Heading style at all. Detecting that
            # is what keeps structure recovery working on non-template files;
            # the flag tells Phase 2 to trust these less.
            block_type = "heading"
            flags.append("heading_inferred")

        builder.add(
            type=block_type,  # type: ignore[arg-type]
            text=text,
            level=level,
            locator=Locator(xml_path=f"/w:document/w:body/w:p[{index}]"),
            style=style,
            flags=flags,
        )
        if block_type == "heading" and not text.strip():
            warnings.append(f"empty heading at w:p[{index}]")

    def _add_table(self, builder: IRBuilder, el: Element, index: int, styles: StyleTable) -> None:
        rows: list[str] = []
        for row in el.findall(qn("w:tr")):
            cells: list[str] = []
            for cell in row.findall(qn("w:tc")):
                cell_text = " ".join(
                    normalise_ws(_paragraph_text(p)) for p in cell.findall(qn("w:p"))
                )
                cells.append(cell_text.strip())
            rows.append("\t".join(cells))
        text = "\n".join(r for r in rows if r.strip())
        if not text:
            return
        ppr_style = styles.resolve(None)
        builder.add(
            type="table",
            text=text,
            locator=Locator(xml_path=f"/w:document/w:body/w:tbl[{index}]"),
            style=BlockStyle(font=ppr_style.get("font"), size_pt=ppr_style.get("size_pt")),
        )

    def _add_footnotes(
        self, builder: IRBuilder, footnotes_xml: bytes | None, warnings: list[str]
    ) -> None:
        if footnotes_xml is None:
            return
        try:
            root = safe_fromstring(footnotes_xml, forbid_dtd=True)
        except Exception as exc:  # footnote part is optional and often broken
            warnings.append(f"footnotes.xml unreadable: {exc}")
            return
        for footnote in root.findall(qn("w:footnote")):
            raw_id = footnote.get(qn("w:id"))
            note_id = _int(raw_id)
            if note_id is not None and note_id <= 0:
                continue  # separator / continuationSeparator
            text = normalise_ws(" ".join(_paragraph_text(p) for p in footnote.findall(qn("w:p"))))
            if not text:
                continue
            builder.add(
                type="footnote",
                text=text,
                locator=Locator(xml_path=f"/w:footnotes/w:footnote[@w:id='{raw_id}']"),
            )

    # -- helpers ---------------------------------------------------------
    def _first_run_props(self, paragraph: Element, theme_fonts: dict[str, str]) -> dict[str, Any]:
        for run in paragraph.findall(qn("w:r")):
            text_el = run.find(qn("w:t"))
            if text_el is None or not (text_el.text or ""):
                continue
            props: dict[str, Any] = {}
            _run_props(_child(run, "w:rPr"), props, theme_fonts)
            return props
        return {}

    def _is_heading(self, style_id: str | None, style_name: str, resolved: dict[str, Any]) -> bool:
        if style_name in TITLE_NAMES:
            return True
        if HEADING_STYLE_RE.match(style_name):
            return True
        if style_id and HEADING_STYLE_ID_RE.match(style_id.replace(" ", "")):
            return True
        return bool(resolved.get("outline_level"))

    def _heading_level(
        self, style_id: str | None, style_name: str, resolved: dict[str, Any]
    ) -> int:
        match = HEADING_STYLE_RE.match(style_name)
        if match:
            return min(int(match.group(1)), 6)
        if style_id:
            match = HEADING_STYLE_ID_RE.match(style_id.replace(" ", ""))
            if match:
                return min(int(match.group(1)), 6)
        outline = resolved.get("outline_level")
        if isinstance(outline, int):
            return min(outline, 6)
        return 1

    def _is_list(self, paragraph: Element, style_name: str, text: str) -> bool:
        ppr = _child(paragraph, "w:pPr")
        if ppr is not None and ppr.find(qn("w:numPr")) is not None:
            return True
        if style_name in LIST_NAMES:
            return True
        return bool(BULLET_RE.match(text))

    def _is_code(self, style_name: str, style: BlockStyle) -> bool:
        if any(hint in style_name for hint in CODE_HINTS):
            return True
        font = (style.font or "").lower()
        return font in MONOSPACE_FONTS

    def _collect_floats(
        self, blocks: list[Block]
    ) -> tuple[list[Figure], list[Table], list[Equation]]:
        """Derive the figure/table/equation registers from blocks and captions.

        Two passes, because a float and its caption are two different blocks:
        captions are authoritative when present (they carry the number), and a
        structural block (``w:tbl``, an image-only paragraph) only creates a
        new register entry when no adjacent caption already claimed it. Without
        that rule every table is counted twice.
        """
        figures: list[Figure] = []
        tables: list[Table] = []
        equations: list[Equation] = []
        consumed: set[str] = set()

        for block in blocks:
            if block.type == "equation":
                equations.append(
                    Equation(
                        id=f"e{len(equations) + 1:03d}",
                        number=_equation_number(block.text),
                        page=block.locator.page,
                    )
                )
                continue
            if block.type != "caption":
                continue
            match = CAPTION_TEXT_RE.match(block.text)
            if not match:
                continue
            consumed.add(block.id)
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
            neighbour = _neighbour_caption_block(blocks, position)
            if neighbour is not None and neighbour.id in consumed:
                continue
            number, caption = None, None
            if neighbour is not None:
                match = CAPTION_TEXT_RE.match(neighbour.text)
                number = match.group("num") if match else None
                caption = neighbour.text
                consumed.add(neighbour.id)
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
        return figures, tables, equations

    def _body_size(self, body: Element, styles: StyleTable) -> float:
        """Character-weighted modal body size — the baseline for heading inference."""
        weights: dict[float, int] = {}
        for paragraph in body.findall(qn("w:p")):
            style_id = _val(_child(_child(paragraph, "w:pPr"), "w:pStyle"))
            props = styles.resolve(style_id)
            props.update(self._first_run_props(paragraph, styles.theme_fonts))
            size = props.get("size_pt")
            if not size:
                continue
            text = normalise_ws(_paragraph_text(paragraph))
            if not text:
                continue
            weights[float(size)] = weights.get(float(size), 0) + len(text)
        if not weights:
            return 0.0
        return max(weights.items(), key=lambda kv: kv[1])[0]

    def _assign_inferred_heading_levels(self, builder: IRBuilder) -> None:
        """Rank inferred headings by font size: largest → level 1."""
        sizes = sorted(
            {
                b.style.size_pt
                for b in builder.blocks
                if "heading_inferred" in b.flags and b.style.size_pt
            },
            reverse=True,
        )
        rank = {size: min(index + 1, 6) for index, size in enumerate(sizes)}
        for block in builder.blocks:
            if "heading_inferred" in block.flags and block.style.size_pt:
                block.level = rank.get(block.style.size_pt, 1)

    def _layout(self, body: Element) -> Layout:
        """Page size and margins from the last section properties element."""
        sect = body.find(qn("w:sectPr"))
        if sect is None:
            for paragraph in reversed(body.findall(qn("w:p"))):
                candidate = _child(_child(paragraph, "w:pPr"), "w:sectPr")
                if candidate is not None:
                    sect = candidate
                    break
        if sect is None:
            return Layout()
        margins: dict[str, float] = {}
        pgmar = _child(sect, "w:pgMar")
        if pgmar is not None:
            for name, attr in (
                ("top", "top"),
                ("bottom", "bottom"),
                ("inner", "left"),
                ("outer", "right"),
                ("gutter", "gutter"),
            ):
                value = _int(pgmar.get(qn(f"w:{attr}")))
                if value is not None:
                    margins[name] = round(value / TWIPS_PER_CM, 2)
        page_size: str | None = None
        pgsz = _child(sect, "w:pgSz")
        if pgsz is not None:
            width = _int(pgsz.get(qn("w:w")))
            height = _int(pgsz.get(qn("w:h")))
            if width and height:
                page_size = _classify_page_size(width / TWIPS_PER_CM, height / TWIPS_PER_CM)
        return Layout(margins_cm=margins, page_size=page_size)


# -- module-level helpers -----------------------------------------------------
def _theme_fonts(theme_xml: bytes | None) -> dict[str, str]:
    """Map OOXML theme slots (``minorHAnsi``/``majorHAnsi``) to real font names."""
    if theme_xml is None:
        return {}
    try:
        root = safe_fromstring(theme_xml, forbid_dtd=True)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for slot, tag in (("major", "a:majorFont"), ("minor", "a:minorFont")):
        font_el = root.find(f".//{qn(tag)}/{qn('a:latin')}")
        if font_el is not None:
            typeface = font_el.get("typeface")
            if typeface:
                out[f"{slot}HAnsi"] = typeface
                out[f"{slot}Latin"] = typeface
    return out


def _read_optional(zf: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return zf.read(name)
    except KeyError:
        return None


def _has_element(el: Element, tags: tuple[str, ...]) -> bool:
    wanted = {qn(tag) for tag in tags}
    return any(node.tag in wanted for node in el.iter())


def _paragraph_text(paragraph: Element) -> str:
    parts: list[str] = []
    t_tag, tab_tag, br_tag, cr_tag = qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")
    nbh_tag, instr_tag = qn("w:noBreakHyphen"), qn("w:instrText")
    for node in paragraph.iter():
        tag = node.tag
        if tag == t_tag:
            parts.append(node.text or "")
        elif tag in (tab_tag, br_tag, cr_tag):
            parts.append(" ")
        elif tag == nbh_tag:
            parts.append("-")
        elif tag == instr_tag:
            continue  # field codes (TOC/PAGEREF) are not document prose
    return "".join(parts)


def _math_text(el: Element) -> str:
    return "".join(node.text or "" for node in el.iter() if node.tag == qn("m:t"))


def _equation_number(text: str) -> str | None:
    match = re.search(r"[\(\[]\s*([\d]+(?:\.[\d]+)*)\s*[\)\]]\s*$", text.strip())
    return match.group(1) if match else None


def _looks_like_manual_heading(text: str, style: BlockStyle, body_size: float) -> bool:
    """Conservative manual-heading test (bold/bigger, short, no terminal period)."""
    if not text or len(text) > 100 or body_size <= 0:
        return False
    if text[-1] in ".,;:":
        return False
    size = style.size_pt or 0.0
    if size >= body_size * 1.15:
        return True
    return bool(style.bold) and size >= body_size and len(text) <= 60


def _neighbour_caption_block(blocks: list[Block], position: int) -> Block | None:
    """Nearest numbered caption within two blocks (captions sit above tables
    and below figures, so both directions are searched)."""
    for offset in (-1, 1, -2, 2):
        index = position + offset
        if 0 <= index < len(blocks):
            candidate = blocks[index]
            if candidate.type == "caption" and CAPTION_TEXT_RE.match(candidate.text):
                return candidate
    return None


def _classify_page_size(width_cm: float, height_cm: float) -> str:
    def close(a: float, b: float, tol: float = 0.35) -> bool:
        return abs(a - b) <= tol

    if close(width_cm, 21.0) and close(height_cm, 29.7):
        return "A4"
    if close(width_cm, 21.59) and close(height_cm, 27.94):
        return "Letter"
    if close(width_cm, 14.8) and close(height_cm, 21.0):
        return "A5"
    return f"{width_cm:.1f}x{height_cm:.1f}cm"


def _page_count(app_xml: bytes | None) -> int:
    """Word/LibreOffice record the rendered page count in docProps/app.xml."""
    if app_xml is None:
        return 0
    try:
        root = safe_fromstring(app_xml, forbid_dtd=True)
    except Exception:
        return 0
    pages = root.find(qn("ep:Pages"))
    value = _int(_text_of(pages))
    return max(value or 0, 0)
