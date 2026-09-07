"""LaTeX → DocumentIR (Phase 1, week 5).

**Security rule that shapes the whole module: we never invoke a TeX engine.**
``\\write18`` shell escape therefore has no path to execution, and the parser
is a text walker over ``.tex`` sources (doc 02 §3). Includes are resolved only
inside the upload root, with a depth cap and a visited-set cycle guard.

What LaTeX gives us that no other format does: explicit semantics. ``\\section``,
``\\cite``, ``\\ref``, ``\\label`` and environments are declarations rather than
inferred structure, which is why the LaTeX parser needs no heading induction at
all. What it does *not* give us is rendered geometry — float numbers, page
numbers and final fonts are properties of a compiled document, so
``locator.page`` stays ``None`` and float ``number`` is only filled when the
author wrote an explicit ``\\tag``.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pylatexenc.latexwalker import (
    LatexCharsNode,
    LatexCommentNode,
    LatexEnvironmentNode,
    LatexGroupNode,
    LatexMacroNode,
    LatexMathNode,
    LatexSpecialsNode,
    LatexWalker,
)

from app.core.config import Settings, get_settings
from app.ingestion.base import BaseParser
from app.ingestion.builder import IRBuilder, file_sha256, normalise_ws
from app.ingestion.guards import check_file_size, check_latex_project, resolve_include
from app.ingestion.references import confidence_for, parse_reference
from app.models.ir import (
    BlockStyle,
    Citation,
    DocumentIR,
    Equation,
    Figure,
    Locator,
    ParsedReference,
    Reference,
    Span,
    Table,
)

# -- macro classes ------------------------------------------------------------
CITATION_MACROS = frozenset(
    {
        "cite",
        "citep",
        "citet",
        "citealp",
        "citealt",
        "citeauthor",
        "citeyear",
        "autocite",
        "parencite",
        "textcite",
        "footcite",
        "smartcite",
        "supercite",
        "nocite",
        "Citep",
        "Citet",
        "Autocite",
        "Parencite",
        "Textcite",
        "Cite",
    }
)
REF_MACROS = frozenset({"ref", "eqref", "pageref", "cref", "Cref", "autoref", "labelcref", "vref"})
SKIP_MACROS = frozenset(
    {
        "label",
        "index",
        "includegraphics",
        "bibliographystyle",
        "bibliography",
        "addcontentsline",
        "setcounter",
        "newpage",
        "clearpage",
        "pagebreak",
        "linebreak",
        "hspace",
        "vspace",
        "smallskip",
        "medskip",
        "bigskip",
        "noindent",
        "centering",
        "raggedright",
        "raggedleft",
        "maketitle",
        "titlepage",
        "input",
        "include",
        "includeonly",
        "graphicspath",
        "tableofcontents",
        "listoffigures",
        "listoftables",
        "appendix",
        "markboth",
        "markright",
        "thispagestyle",
        "pagestyle",
        "phantom",
    }
)
#: macros whose single braced argument is the text we want
TEXT_ARG_MACROS = frozenset(
    {
        "emph",
        "textbf",
        "textit",
        "textsc",
        "texttt",
        "textsf",
        "textrm",
        "textup",
        "textmd",
        "textsl",
        "underline",
        "uline",
        "textsuperscript",
        "textsubscript",
        "textnormal",
        "mbox",
        "text",
        "textpl",
        "enquote",
        "textquote",
        "captionof",
        "footnote",
        "footnotetext",
        "caption",
    }
)
FOOTNOTE_MACROS = frozenset({"footnote", "footnotetext"})
LITERAL_MACROS: dict[str, str] = {
    "LaTeX": "LaTeX",
    "TeX": "TeX",
    "LaTeXe": "LaTeX2e",
    "S": "§",
    "P": "¶",
    "dots": "…",
    "ldots": "…",
    "cdots": "…",
    "textellipsis": "…",
    "textbackslash": "\\",
    "textendash": "–",
    "textemdash": "—",
    "textquotesingle": "'",
    "textquotedblleft": "„",
    "textquotedblright": "”",
    "textquoteleft": "‚",
    "textquoteright": "’",
    "textdegree": "°",
    "texttimes": "×",
    "textpm": "±",
    "textapprox": "≈",
    "texteuro": "€",
    "%": "%",
    "&": "&",
    "#": "#",
    "$": "$",
    "_": "_",
    "{": "{",
    "}": "}",
    " ": " ",
    ",": " ",
    ";": " ",
    ":": " ",
    "!": "",
    "AA": "Å",
    "aa": "å",
    "AE": "Æ",
    "ae": "æ",
    "OE": "Œ",
    "oe": "œ",
    "ss": "ß",
    "l": "ł",
    "L": "Ł",
    "o": "ø",
    "O": "Ø",
    "c": "",
    "k": "",
    "v": "",
    "u": "",
    "t": "",
    "H": "",
    "dot": "",
}
SPECIALS: dict[str, str] = {"~": " ", "&": "&", "_": "_", "#": "#", "$": "$", "%": "%"}

#: sectioning macro → level in a ``report``/``book`` class (chapter-based).
#: Documents without ``\\chapter`` are shifted up one by ``_apply_heading_levels``,
#: so ``\\section`` becomes level 1 there — matching how students number them.
SECTION_BASE_LEVEL: dict[str, int] = {
    "part": 1,
    "chapter": 1,
    "section": 2,
    "subsection": 3,
    "subsubsection": 4,
    "paragraph": 5,
    "subparagraph": 6,
}
FLOAT_ENVS = frozenset({"figure", "figure*", "wrapfigure", "subfigure"})
TABLE_ENVS = frozenset({"table", "table*", "wraptable", "longtable", "tabular", "tabularx"})
CODE_ENVS = frozenset(
    {"lstlisting", "verbatim", "Verbatim", "minted", "lstlisting*", "alltt", "spverbatim"}
)
QUOTE_ENVS = frozenset({"quote", "quotation", "verse", "csquote", "displayquote"})
LIST_ENVS = frozenset({"itemize", "enumerate", "description", "compactitem", "compactenum"})
MATH_ENVS = frozenset(
    {
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
        "displaymath",
        "math",
        "flalign",
        "flalign*",
        "alignat",
        "alignat*",
        "subequations",
        "IEEEeqnarray",
    }
)
#: environments that are structural wrappers: render their body in place
TRANSPARENT_ENVS = frozenset(
    {
        "center",
        "flushleft",
        "flushright",
        "minipage",
        "document",
        "sloppypar",
        "small",
        "footnotesize",
        "spacing",
        "adjustwidth",
        "samepage",
        "figuregroup",
    }
)

DOCUMENTCLASS_RE = re.compile(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
USEPACKAGE_RE = re.compile(r"\\usepackage\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
BIBLIOGRAPHY_RE = re.compile(r"\\(?:bibliography|addbibresource)\s*\{([^}]*)\}")
BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
TITLE_RE = re.compile(r"\\title\s*\{((?:[^{}]|\{[^{}]*\})*)\}")
AUTHOR_RE = re.compile(r"\\author\s*\{((?:[^{}]|\{[^{}]*\})*)\}")


@dataclass
class _Unit:
    """One .tex file in include order, with an offset base for absolute positions."""

    path: Path
    text: str
    base: int
    line_starts: list[int]
    depth: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class _PendingHeading:
    block_index: int
    base_level: int


class LatexParser(BaseParser):
    """``.tex`` project → DocumentIR."""

    format = "latex"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # -- entry point -----------------------------------------------------
    def parse(self, path: Path, doc_id: str, filename: str) -> DocumentIR:
        # resolved: include targets are validated against this root, and mixing a
        # relative root with absolute resolved paths makes relative_to() raise.
        root = path.parent.resolve()
        check_file_size(path, self.settings)
        check_latex_project(path, root, self.settings)

        main_text = _read_text(path)
        units, skipped_inputs, warnings = self._collect_units(path, main_text, root)

        preamble = main_text[: max(main_text.find(r"\begin{document}"), 0)]
        builder = IRBuilder(
            doc_id=doc_id,
            source_format="latex",
            filename=filename,
            sha256=file_sha256(path),
        )
        state = _ParseState()

        for unit in units:
            self._parse_unit(unit, builder, state)

        self._apply_heading_levels(builder, state)
        self._link_labels(state)

        references = self._collect_references(root, main_text, state, warnings)
        citations = self._finalise_citations(builder, state)

        ir = builder.build(
            pages=0,
            extra={
                "latex": {
                    "documentclass": _documentclass(preamble),
                    "class_options": _class_options(preamble),
                    "packages": _packages(preamble),
                    "units": [_rel_or_name(u.path, root) for u in units],
                    "inputs_skipped": skipped_inputs,
                    "title": _title(preamble),
                    "author": _author(preamble),
                    "float_labels": state.float_labels,
                },
                "parse_warnings": [*warnings, *state.warnings],
            },
        )
        ir.references = references
        ir.citations = citations
        ir.figures = state.figures
        ir.tables = state.tables
        ir.equations = state.equations
        return ir

    # -- includes --------------------------------------------------------
    def _collect_units(
        self, main_path: Path, main_text: str, root: Path
    ) -> tuple[list[_Unit], list[str], list[str]]:
        """DFS over ``\\input``/``\\include`` inside the sandbox."""
        units: list[_Unit] = []
        skipped: list[str] = []
        warnings: list[str] = []
        seen: set[Path] = set()
        queue: list[tuple[Path, str, int]] = [(main_path, main_text, 0)]

        while queue:
            current_path, text, depth = queue.pop(0)
            resolved = current_path.resolve()
            if resolved in seen:
                warnings.append(f"include cycle ignored: {current_path.name}")
                continue
            seen.add(resolved)
            units.append(
                _Unit(
                    path=resolved,
                    text=text,
                    base=0,
                    line_starts=_line_starts(text),
                    depth=depth,
                )
            )
            if depth >= self.settings.max_latex_include_depth:
                warnings.append(f"include depth cap reached at {current_path.name}")
                continue
            for name in _include_targets(text):
                target = resolve_include(name, root, current_path)
                if target is None:
                    skipped.append(name)
                    warnings.append(f"include outside sandbox or missing: {name!r}")
                    continue
                if target.resolve() in seen:
                    warnings.append(f"include cycle ignored: {target.name}")
                    continue
                queue.append((target, _read_text(target), depth + 1))
            if len(units) > self.settings.max_latex_files:
                warnings.append("too many included files; truncating")
                break
        return units, skipped, warnings

    # -- per-file parsing ------------------------------------------------
    def _parse_unit(self, unit: _Unit, builder: IRBuilder, state: _ParseState) -> None:
        text = unit.text
        marker = r"\begin{document}"
        body_start = text.find(marker)
        body_end = text.find(r"\end{document}")
        # start *after* the marker so the body's contents are top-level nodes;
        # including it would wrap everything in a `document` environment.
        start = body_start + len(marker) if body_start != -1 else 0
        end = body_end if body_end != -1 else len(text)
        if start >= end:
            # no \begin{document}: treat the whole file as body (common for
            # single-file snippets and for every \input child)
            start, end = 0, len(text)
        chunk = text[start:end]

        try:
            walker = LatexWalker(chunk)
            nodes, _, _ = walker.get_latex_nodes()
        except Exception as exc:
            state.warnings.append(f"LaTeX walker failed on {unit.path.name}: {exc}")
            return

        buffer: list[Any] = []
        buffer_start: int | None = None

        def flush() -> None:
            nonlocal buffer, buffer_start
            if not buffer:
                buffer_start = None
                return
            pos = buffer_start if buffer_start is not None else _node_pos(buffer[0])
            self._emit_paragraph(builder, unit, state, buffer, pos)
            buffer, buffer_start = [], None

        for node in nodes:
            if isinstance(node, LatexMacroNode):
                name = (node.macroname or "").rstrip("*")
                starred = (node.macroname or "").endswith("*") or _is_starred(node)
                if name in SECTION_BASE_LEVEL:
                    flush()
                    self._emit_heading(builder, unit, state, node, name, starred)
                    continue
                if name in SKIP_MACROS and name in (
                    "tableofcontents",
                    "listoffigures",
                    "listoftables",
                ):
                    flush()
                    self._emit_structural_placeholder(builder, unit, state, node, name)
                    continue
            if isinstance(node, LatexEnvironmentNode):
                flush()
                self._emit_environment(builder, unit, state, node)
                continue
            if isinstance(node, LatexCharsNode) and "\n\n" in (node.chars or ""):
                # blank line: paragraph break inside a chars node
                pieces = (node.chars or "").split("\n\n")
                for index, piece in enumerate(pieces):
                    if index:
                        flush()
                    if piece.strip():
                        if buffer_start is None:
                            buffer_start = _node_pos(node)
                        buffer.append(LatexCharsNode(chars=piece))
                continue
            if buffer_start is None:
                buffer_start = _node_pos(node)
            buffer.append(node)
        flush()

    def _emit_paragraph(
        self,
        builder: IRBuilder,
        unit: _Unit,
        state: _ParseState,
        nodes: list[Any],
        pos: int,
    ) -> None:
        renderer = _Renderer()
        text = renderer.render(nodes)
        if not text:
            return
        block = builder.add(
            type="paragraph",
            text=text,
            locator=_locator(unit, pos),
            style=BlockStyle(),
        )
        state.register(len(builder.blocks) - 1, block, renderer)
        emit_footnotes(builder, unit, renderer, pos)

    def _emit_heading(
        self,
        builder: IRBuilder,
        unit: _Unit,
        state: _ParseState,
        node: LatexMacroNode,
        name: str,
        starred: bool,
    ) -> None:
        renderer = _Renderer()
        title = renderer.render(_mandatory_arg(node))
        if not title:
            state.warnings.append(f"empty heading from \\{name}")
            return
        block = builder.add(
            type="heading",
            text=title,
            level=1,  # fixed up in _apply_heading_levels
            locator=_locator(unit, _node_pos(node)),
            style=BlockStyle(),
            flags=["starred"] if starred else [],
        )
        state.pending_headings.append(
            _PendingHeading(len(builder.blocks) - 1, SECTION_BASE_LEVEL[name])
        )
        state.seen_section_macros.add(name)
        state.register(len(builder.blocks) - 1, block, renderer)

    def _emit_structural_placeholder(
        self, builder: IRBuilder, unit: _Unit, state: _ParseState, node: LatexMacroNode, name: str
    ) -> None:
        """ToC / list-of-figures commands produce rendered content we cannot see.

        We record the intent as a warning instead of inventing text, so the
        report can say why the IR has no ToC blocks for a LaTeX source while
        the exclusion mask still covers the rendered ToC in PDF/DOCX.
        """
        del builder, unit
        state.warnings.append(f"\\{name} present: rendered ToC/list not represented in the IR")

    def _emit_environment(
        self, builder: IRBuilder, unit: _Unit, state: _ParseState, node: LatexEnvironmentNode
    ) -> None:
        name = node.environmentname or ""
        if name in CODE_ENVS:
            self._emit_code(builder, unit, state, node, name)
        elif name in QUOTE_ENVS:
            self._emit_quote(builder, unit, state, node)
        elif name in LIST_ENVS:
            self._emit_list(builder, unit, state, node)
        elif name in MATH_ENVS:
            self._emit_equation(builder, unit, state, node, name)
        elif name in FLOAT_ENVS:
            self._emit_figure(builder, unit, state, node, name)
        elif name in TABLE_ENVS:
            self._emit_table(builder, unit, state, node, name)
        elif name == "abstract":
            self._emit_abstract(builder, unit, state, node)
        elif name == "thebibliography":
            self._emit_bibliography(builder, unit, state, node)
        else:
            # structural wrapper or unknown environment: keep the prose
            self._parse_unit_nodes(builder, unit, state, node.nodelist or [])

    def _parse_unit_nodes(
        self, builder: IRBuilder, unit: _Unit, state: _ParseState, nodes: list[Any]
    ) -> None:
        """Render a nested node list as paragraphs (used for transparent envs)."""
        buffer: list[Any] = []
        buffer_start: int | None = None
        for node in nodes:
            if isinstance(node, LatexMacroNode) and (node.macroname or "") in SECTION_BASE_LEVEL:
                if buffer:
                    self._emit_paragraph(
                        builder,
                        unit,
                        state,
                        buffer,
                        buffer_start if buffer_start is not None else _node_pos(node),
                    )
                    buffer, buffer_start = [], None
                self._emit_heading(
                    builder,
                    unit,
                    state,
                    node,
                    (node.macroname or "").rstrip("*"),
                    _is_starred(node),
                )
                continue
            if isinstance(node, LatexEnvironmentNode):
                if buffer:
                    self._emit_paragraph(
                        builder,
                        unit,
                        state,
                        buffer,
                        buffer_start if buffer_start is not None else _node_pos(node),
                    )
                    buffer, buffer_start = [], None
                self._emit_environment(builder, unit, state, node)
                continue
            if isinstance(node, LatexCharsNode) and "\n\n" in (node.chars or ""):
                for index, piece in enumerate((node.chars or "").split("\n\n")):
                    if index and buffer:
                        self._emit_paragraph(
                            builder,
                            unit,
                            state,
                            buffer,
                            buffer_start if buffer_start is not None else _node_pos(node),
                        )
                        buffer, buffer_start = [], None
                    if piece.strip():
                        if buffer_start is None:
                            buffer_start = _node_pos(node)
                        buffer.append(LatexCharsNode(chars=piece))
                continue
            if buffer_start is None:
                buffer_start = _node_pos(node)
            buffer.append(node)
        if buffer:
            self._emit_paragraph(
                builder,
                unit,
                state,
                buffer,
                buffer_start if buffer_start is not None else _node_pos(nodes[0]),
            )

    def _emit_code(
        self,
        builder: IRBuilder,
        unit: _Unit,
        state: _ParseState,
        node: LatexEnvironmentNode,
        name: str,
    ) -> None:
        raw = _verbatim_body(node.latex_verbatim(), name) or ""
        text = raw.strip("\n")
        if not text.strip():
            return
        block = builder.add(
            type="code",
            text=text,
            locator=_locator(unit, _node_pos(node)),
            style=BlockStyle(),
            flags=[f"env:{name}"],
        )
        state.register(len(builder.blocks) - 1, block, _Renderer())

    def _emit_quote(
        self, builder: IRBuilder, unit: _Unit, state: _ParseState, node: LatexEnvironmentNode
    ) -> None:
        renderer = _Renderer()
        text = normalise_ws(renderer.render(node.nodelist or []))
        if not text:
            return
        block = builder.add(
            type="quote",
            text=text,
            locator=_locator(unit, _node_pos(node)),
            style=BlockStyle(),
        )
        state.register(len(builder.blocks) - 1, block, renderer)
        emit_footnotes(builder, unit, renderer, _node_pos(node))

    def _emit_list(
        self, builder: IRBuilder, unit: _Unit, state: _ParseState, node: LatexEnvironmentNode
    ) -> None:
        """Each ``\\item`` becomes a ``list_item`` block; nested lists recurse."""
        items = _split_items(node.nodelist or [])
        for item_nodes in items:
            if not item_nodes:
                continue
            if len(item_nodes) == 1 and isinstance(item_nodes[0], LatexEnvironmentNode):
                self._emit_environment(builder, unit, state, item_nodes[0])
                continue
            renderer = _Renderer()
            text = renderer.render(item_nodes)
            if not text:
                continue
            block = builder.add(
                type="list_item",
                text=text,
                locator=_locator(unit, _node_pos(item_nodes[0])),
                style=BlockStyle(),
            )
            state.register(len(builder.blocks) - 1, block, renderer)
            emit_footnotes(builder, unit, renderer, _node_pos(item_nodes[0]))

    def _emit_equation(
        self,
        builder: IRBuilder,
        unit: _Unit,
        state: _ParseState,
        node: LatexEnvironmentNode,
        name: str,
    ) -> None:
        raw = node.latex_verbatim()
        text = normalise_ws(_math_body(raw, name))
        if not text:
            return
        number = _tag_number(raw)
        block = builder.add(
            type="equation",
            text=text,
            locator=_locator(unit, _node_pos(node)),
            style=BlockStyle(),
            flags=[f"env:{name}"],
        )
        state.register(len(builder.blocks) - 1, block, _Renderer())
        seq = len(state.equations) + 1
        equation = Equation(id=f"e{seq:03d}", number=number)
        state.equations.append(equation)
        for label in _labels_in(raw):
            state.label_to_float[label] = ("equation", equation.id)

    def _emit_figure(
        self,
        builder: IRBuilder,
        unit: _Unit,
        state: _ParseState,
        node: LatexEnvironmentNode,
        name: str,
    ) -> None:
        caption, label = _caption_and_label(node)
        renderer = _Renderer()
        caption_text = renderer.render(caption) if caption else ""
        if caption_text:
            block = builder.add(
                type="caption",
                text=caption_text,
                locator=_locator(unit, _node_pos(node)),
                style=BlockStyle(),
                flags=[f"float:{name}"],
            )
            state.register(len(builder.blocks) - 1, block, renderer)
        else:
            block = builder.add(
                type="figure",
                text="",
                locator=_locator(unit, _node_pos(node)),
                style=BlockStyle(),
                flags=[f"float:{name}", "no_caption"],
            )
        seq = len(state.figures) + 1
        figure = Figure(id=f"f{seq:03d}", number=None, caption=caption_text or None)
        state.figures.append(figure)
        if label:
            state.label_to_float[label] = ("figure", figure.id)
            state.float_labels[figure.id] = label

    def _emit_table(
        self,
        builder: IRBuilder,
        unit: _Unit,
        state: _ParseState,
        node: LatexEnvironmentNode,
        name: str,
    ) -> None:
        caption, label = _caption_and_label(node)
        renderer = _Renderer()
        caption_text = renderer.render(caption) if caption else ""
        body_text = renderer.render(node.nodelist or [])
        body_text = re.sub(r"\s*&\s*", "\t", body_text)
        if caption_text and body_text.startswith(caption_text):
            body_text = body_text[len(caption_text) :].strip()
        text = body_text or caption_text
        if not text:
            return
        block = builder.add(
            type="table",
            text=text,
            locator=_locator(unit, _node_pos(node)),
            style=BlockStyle(),
            flags=[f"float:{name}"],
        )
        state.register(len(builder.blocks) - 1, block, renderer)
        seq = len(state.tables) + 1
        table = Table(id=f"t{seq:03d}", number=None, caption=caption_text or None)
        state.tables.append(table)
        if label:
            state.label_to_float[label] = ("table", table.id)
            state.float_labels[table.id] = label

    def _emit_abstract(
        self, builder: IRBuilder, unit: _Unit, state: _ParseState, node: LatexEnvironmentNode
    ) -> None:
        builder.add(
            type="heading",
            text="Abstract",
            level=1,
            locator=_locator(unit, _node_pos(node)),
            style=BlockStyle(),
        )
        # chapter-level: an abstract sits alongside \chapter, never under it
        state.pending_headings.append(_PendingHeading(len(builder.blocks) - 1, 1))
        self._parse_unit_nodes(builder, unit, state, node.nodelist or [])

    def _emit_bibliography(
        self, builder: IRBuilder, unit: _Unit, state: _ParseState, node: LatexEnvironmentNode
    ) -> None:
        raw = node.latex_verbatim()
        entries = list(BIBITEM_RE.finditer(raw))
        if not entries:
            return
        builder.add(
            type="heading",
            text="Bibliografia",
            level=1,
            locator=_locator(unit, _node_pos(node)),
            style=BlockStyle(),
        )
        state.pending_headings.append(_PendingHeading(len(builder.blocks) - 1, 1))
        end_marker = raw.find(r"\end{thebibliography}")
        limit = end_marker if end_marker != -1 else len(raw)
        for index, match in enumerate(entries):
            key = match.group(1).strip()
            start = match.end()
            stop = entries[index + 1].start() if index + 1 < len(entries) else limit
            entry_text = normalise_ws(_strip_macros(raw[start : min(stop, limit)]))
            if not entry_text:
                continue
            block = builder.add(
                type="bibitem",
                text=entry_text,
                locator=_locator(unit, _node_pos(node) + start),
                style=BlockStyle(),
                flags=[f"bibkey:{key}"],
            )
            state.bibitems.append((block, key, entry_text))

    # -- post-processing -------------------------------------------------
    def _apply_heading_levels(self, builder: IRBuilder, state: _ParseState) -> None:
        chaptered = bool(state.seen_section_macros & {"chapter", "part"})
        shift = 0 if chaptered else -1
        for pending in state.pending_headings:
            block = builder.blocks[pending.block_index]
            block.level = max(1, min(pending.base_level + shift, 6))

    def _link_labels(self, state: _ParseState) -> None:
        for block_id, label in state.block_refs:
            target = state.label_to_float.get(label)
            if target is None:
                continue
            kind, float_id = target
            registry = state.figures if kind == "figure" else state.tables
            for item in registry:
                if item.id == float_id and block_id not in item.referenced_by:
                    item.referenced_by.append(block_id)

    def _finalise_citations(self, builder: IRBuilder, state: _ParseState) -> list[Citation]:
        """Convert the renderer's citation marks into IR citations.

        ``\\cite{a,b}`` is rendered as ``[a, b]`` so the plain text stays
        readable and the span still points at something the reader can see;
        ``raw`` keeps the original macro so Phase 3 can report precisely.
        """
        citations: list[Citation] = []
        blocks_by_index = {index: block for index, block in enumerate(builder.blocks)}
        seq = 0
        for block_index, hit in state.citation_hits:
            block = blocks_by_index.get(block_index)
            if block is None:
                continue
            seq += 1
            citations.append(
                Citation(
                    id=f"c{seq:04d}",
                    raw=hit.raw,
                    style="bibtex_key",
                    block_id=block.id,
                    span=Span(start=block.span.start + hit.start, end=block.span.start + hit.end),
                    ref_id=state.bibkey_to_ref.get(hit.keys[0]) if hit.keys else None,
                )
            )
        return citations

    def _collect_references(
        self, root: Path, main_text: str, state: _ParseState, warnings: list[str]
    ) -> list[Reference]:
        """``.bib`` first (structured, exact), ``thebibliography`` as fallback."""
        references: list[Reference] = []
        for bib_path in _bib_paths(root, main_text):
            try:
                entries = _load_bib(bib_path)
                raws = _split_bib_entries(bib_path.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:  # a broken .bib must not kill ingestion
                warnings.append(f"could not parse {bib_path.name}: {exc}")
                continue
            for entry in entries:
                key = str(entry.get("ID") or entry.get("id") or "").strip()
                parsed = _bibentry_to_parsed(entry)
                seq = len(references) + 1
                ref_id = key or f"r{seq:03d}"
                references.append(
                    Reference(
                        id=ref_id,
                        raw=raws.get(key) or _bibentry_to_raw(entry),
                        parsed=parsed,
                        confidence=confidence_for(parsed),
                    )
                )
                if key:
                    state.bibkey_to_ref[key] = ref_id

        for _block, key, entry_text in state.bibitems:
            parsed, confidence = parse_reference(entry_text)
            seq = len(references) + 1
            ref_id = key or f"r{seq:03d}"
            references.append(
                Reference(id=ref_id, raw=entry_text, parsed=parsed, confidence=confidence)
            )
            if key and key not in state.bibkey_to_ref:
                state.bibkey_to_ref[key] = ref_id
        return references


# -- parse state --------------------------------------------------------------
@dataclass
class _ParseState:
    pending_headings: list[_PendingHeading] = field(default_factory=list)
    seen_section_macros: set[str] = field(default_factory=set)
    figures: list[Figure] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    equations: list[Equation] = field(default_factory=list)
    label_to_float: dict[str, tuple[str, str]] = field(default_factory=dict)
    float_labels: dict[str, str] = field(default_factory=dict)
    block_refs: list[tuple[str, str]] = field(default_factory=list)
    citation_hits: list[tuple[int, _CitationMark]] = field(default_factory=list)
    bibitems: list[tuple[Any, str, str]] = field(default_factory=list)
    bibkey_to_ref: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def register(self, block_index: int, block: Any, renderer: _Renderer) -> None:
        """Record the citation marks and \\ref targets a block produced."""
        for mark in renderer.citations:
            self.citation_hits.append((block_index, mark))
        for label in renderer.refs:
            self.block_refs.append((block.id, label))


@dataclass(frozen=True)
class _CitationMark:
    raw: str
    keys: tuple[str, ...]
    start: int
    end: int


# -- renderer -----------------------------------------------------------------
class _Renderer:
    """Node list → readable plain text, recording citations and ``\\ref`` targets.

    Deliberately not ``LatexNodes2Text``: its defaults render ``\\cite`` as
    ``<cit.>`` and ``\\section`` as ``§ TITLE``, which is fine for a terminal
    preview and wrong for a document IR.
    """

    def __init__(self) -> None:
        self._out: list[str] = []
        self._length = 0
        self.citations: list[_CitationMark] = []
        self.refs: list[str] = []
        self.footnotes: list[str] = []

    def _emit(self, text: str) -> None:
        """Append *text*, collapsing whitespace as it goes.

        Normalising here rather than at the end is what keeps the recorded
        citation offsets valid: ``\\cite`` spans point into the string this
        method builds, so any later ``normalise_ws`` would shift them all.
        """
        if not text:
            return
        collapsed = re.sub(r"\s+", " ", text)
        if self._length == 0:
            collapsed = collapsed.lstrip(" ")
        elif collapsed.startswith(" ") and self._out and self._out[-1].endswith(" "):
            collapsed = collapsed[1:]
        if not collapsed:
            return
        self._out.append(collapsed)
        self._length += len(collapsed)

    def render(self, nodes: list[Any]) -> str:
        self._render_nodes(nodes)
        return "".join(self._out).strip()

    def _render_nodes(self, nodes: list[Any]) -> None:
        for node in nodes:
            self._render_node(node)

    def _render_node(self, node: Any) -> None:
        if isinstance(node, LatexCharsNode):
            self._emit(node.chars or "")
            return
        if isinstance(node, LatexCommentNode):
            return
        if isinstance(node, LatexSpecialsNode):
            self._emit(SPECIALS.get(node.specials_chars or "", " "))
            return
        if isinstance(node, LatexMathNode):
            self._emit(_math_text(node))
            return
        if isinstance(node, LatexGroupNode):
            self._render_nodes(node.nodelist or [])
            return
        if isinstance(node, LatexEnvironmentNode):
            name = node.environmentname or ""
            if name in CODE_ENVS:
                self._emit(normalise_ws(_verbatim_body(node.latex_verbatim(), name) or ""))
                return
            self._render_nodes(node.nodelist or [])
            return
        if isinstance(node, LatexMacroNode):
            self._render_macro(node)
            return

    def _render_macro(self, node: LatexMacroNode) -> None:
        name = node.macroname or ""
        base = name.rstrip("*")

        if base in CITATION_MACROS:
            keys = _citation_keys(node)
            rendered = "[" + ", ".join(keys) + "]" if keys else ""
            start = self._length
            self._emit(rendered)
            if keys:
                self.citations.append(
                    _CitationMark(
                        raw=_macro_raw(node), keys=tuple(keys), start=start, end=self._length
                    )
                )
            return

        if base in REF_MACROS:
            label = _first_arg_text(node)
            if base != "label" and label:
                self.refs.append(label)
            return

        if base in FOOTNOTE_MACROS:
            self.footnotes.append(_render_plain(_mandatory_arg(node)))
            return

        if base in LITERAL_MACROS:
            self._emit(LITERAL_MACROS[base])
            return

        if base in TEXT_ARG_MACROS:
            self._render_nodes(_mandatory_arg(node))
            return

        if base in SKIP_MACROS:
            return

        # unknown macro: keep whatever braced argument it carries
        args = _mandatory_arg(node)
        if args:
            self._render_nodes(args)


def _render_plain(nodes: list[Any]) -> str:
    renderer = _Renderer()
    return renderer.render(nodes)


def emit_footnotes(builder: IRBuilder, unit: _Unit, renderer: _Renderer, pos: int) -> None:
    """``\\footnote`` content becomes ``footnote`` blocks, as in DOCX.

    Keeping them out of the paragraph they hang off preserves the invariant
    that a paragraph's text is what a reader sees in the body, while still
    making footnote prose available to the grammar and AIGT modules.
    """
    for text in renderer.footnotes:
        if text:
            builder.add(
                type="footnote",
                text=text,
                locator=_locator(unit, pos),
                style=BlockStyle(),
            )


# -- node helpers -------------------------------------------------------------
def _node_pos(node: Any) -> int:
    return int(getattr(node, "pos", 0) or 0)


def _node_len(node: Any) -> int:
    return int(getattr(node, "len", 0) or 0)


def _is_starred(node: LatexMacroNode) -> bool:
    if (node.macroname or "").endswith("*"):
        return True
    args = list(node.nodeargd.argnlist) if node.nodeargd else []
    return bool(args) and isinstance(args[0], LatexCharsNode) and (args[0].chars or "") == "*"


def _mandatory_arg(node: LatexMacroNode) -> list[Any]:
    """Nodes of the last braced argument (the text-bearing one)."""
    args = list(node.nodeargd.argnlist) if node.nodeargd else []
    for arg in reversed(args):
        if isinstance(arg, LatexGroupNode):
            return list(arg.nodelist or [])
    return []


def _first_arg_text(node: LatexMacroNode) -> str:
    nodes = _mandatory_arg(node)
    if not nodes:
        args = list(node.nodeargd.argnlist) if node.nodeargd else []
        for arg in args:
            if isinstance(arg, LatexCharsNode) and (arg.chars or "").strip():
                return str(arg.chars).strip()
        return ""
    return normalise_ws(_render_plain(nodes))


def _citation_keys(node: LatexMacroNode) -> list[str]:
    raw = _first_arg_text(node)
    return [key.strip() for key in raw.split(",") if key.strip()]


def _macro_raw(node: LatexMacroNode) -> str:
    return f"\\{node.macroname}{{{', '.join(_citation_keys(node))}}}"


def _math_text(node: LatexMathNode) -> str:
    if node.nodelist:
        return normalise_ws(_render_plain(list(node.nodelist)))
    return ""


def _split_items(nodes: list[Any]) -> list[list[Any]]:
    """Split an itemize/enumerate body on ``\\item``."""
    items: list[list[Any]] = []
    current: list[Any] = []
    for node in nodes:
        if isinstance(node, LatexMacroNode) and (node.macroname or "").rstrip("*") == "item":
            if current:
                items.append(current)
            current = []
            continue
        current.append(node)
    if current:
        items.append(current)
    return items


def _caption_and_label(node: LatexEnvironmentNode) -> tuple[list[Any] | None, str | None]:
    """Find ``\\caption{...}`` and ``\\label{...}`` inside a float environment.

    pylatexenc's default macro database has no argument spec for ``\\caption``,
    so the braced group usually arrives as the *next sibling node* rather than
    as a macro argument. Both shapes are handled, because which one you get
    depends on the preamble.
    """
    caption_nodes: list[Any] | None = None
    label: str | None = None
    children = list(node.nodelist or [])
    for index, sub in enumerate(children):
        if isinstance(sub, LatexMacroNode):
            name = (sub.macroname or "").rstrip("*")
            if name == "caption" and caption_nodes is None:
                caption_nodes = _mandatory_arg(sub) or _sibling_group(children, index)
            elif name == "label" and label is None:
                label = _first_arg_text(sub) or _sibling_text(children, index) or None
        elif isinstance(sub, LatexEnvironmentNode) and label is None:
            _, nested_label = _caption_and_label(sub)
            label = nested_label
    return caption_nodes, label


def _sibling_group(children: list[Any], index: int) -> list[Any]:
    """The braced group immediately following ``children[index]``."""
    for follow in children[index + 1 : index + 3]:
        if isinstance(follow, LatexGroupNode):
            return list(follow.nodelist or [])
    return []


def _sibling_text(children: list[Any], index: int) -> str | None:
    nodes = _sibling_group(children, index)
    return normalise_ws(_render_plain(nodes)) if nodes else None


def _labels_in(raw: str) -> list[str]:
    return re.findall(r"\\label\s*\{([^}]*)\}", raw)


def _tag_number(raw: str) -> str | None:
    match = re.search(r"\\tag\s*\{([^}]*)\}", raw)
    return match.group(1).strip() if match else None


def _verbatim_body(raw: str, env: str) -> str | None:
    """The text between ``\\begin{env}`` and ``\\end{env}`` in *raw*."""
    begin = re.search(r"\\begin\{" + re.escape(env) + r"\}(\[[^\]]*\])?(\{[^}]*\})?", raw)
    if not begin:
        return None
    end = raw.find(r"\end{" + env + r"}", begin.end())
    if end == -1:
        return raw[begin.end() :]
    return raw[begin.end() : end]


def _math_body(raw: str, env: str) -> str:
    body = _verbatim_body(raw, env)
    if body is None:
        body = re.sub(r"^\\begin\{[^}]*\}|\\end\{[^}]*\}$", "", raw.strip())
    body = re.sub(r"\\label\s*\{[^}]*\}", " ", body)
    return body


def _strip_macros(text: str) -> str:
    """Drop remaining macros from a bibliography entry (keeps the prose)."""
    text = re.sub(r"\\newblock", ". ", text)
    text = re.sub(r"\\emph\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    text = text.replace("{", "").replace("}", "")
    return text


# -- file helpers -------------------------------------------------------------
def _rel_or_name(path: Path, root: Path) -> str:
    """Path relative to the project root when possible, else just the name."""
    try:
        return str(path.relative_to(root))
    except ValueError:  # pragma: no cover - defensive
        return path.name


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1250", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _locator(unit: _Unit, pos: int) -> Locator:
    absolute = unit.base + pos
    line = bisect_right(unit.line_starts, absolute)
    return Locator(line=max(line, 1), xml_path=str(unit.path.name))


def _include_targets(text: str) -> list[str]:
    targets: list[str] = []
    for match in re.finditer(r"\\(?:input|include|subfile)\s*\{([^}]*)\}", text):
        targets.append(match.group(1).strip())
    return targets


def _documentclass(preamble: str) -> str | None:
    match = DOCUMENTCLASS_RE.search(preamble)
    return match.group(2).strip() if match else None


def _class_options(preamble: str) -> list[str]:
    match = DOCUMENTCLASS_RE.search(preamble)
    if not match or not match.group(1):
        return []
    return [opt.strip() for opt in match.group(1).split(",") if opt.strip()]


def _packages(preamble: str) -> list[str]:
    found: list[str] = []
    for match in USEPACKAGE_RE.finditer(preamble):
        found.extend(pkg.strip() for pkg in match.group(2).split(",") if pkg.strip())
    return found


def _title(preamble: str) -> str | None:
    match = TITLE_RE.search(preamble)
    return normalise_ws(match.group(1)) if match else None


def _author(preamble: str) -> str | None:
    match = AUTHOR_RE.search(preamble)
    return normalise_ws(match.group(1)) if match else None


def _bib_paths(root: Path, main_text: str) -> list[Path]:
    """``\\bibliography{...}`` targets first, then any .bib in the upload."""
    paths: list[Path] = []
    for match in BIBLIOGRAPHY_RE.finditer(main_text):
        for name in match.group(1).split(","):
            name = name.strip()
            if not name:
                continue
            if not name.endswith(".bib"):
                name += ".bib"
            candidate = resolve_include(name, root, root / "main.tex")
            if candidate is not None and candidate not in paths:
                paths.append(candidate)
    if not paths:
        for candidate in sorted(root.glob("*.bib"))[:5]:
            paths.append(candidate.resolve())
    return paths


def _load_bib(path: Path) -> list[dict[str, Any]]:
    import bibtexparser
    from bibtexparser.bparser import BibTexParser

    parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False)
    database = bibtexparser.loads(path.read_text(encoding="utf-8", errors="replace"), parser)
    return list(database.entries)


def _split_bib_entries(text: str) -> dict[str, str]:
    """Map citation key → the literal entry text, via brace counting."""
    raws: dict[str, str] = {}
    for match in re.finditer(r"@\w+\s*\{", text):
        start = match.end()
        depth = 1
        index = start
        while index < len(text) and depth:
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        body = text[start : index - 1]
        key = body.split(",", 1)[0].strip()
        if key:
            raws[key] = "@" + text[match.start() + 1 : index].strip()
    return raws


def _bibentry_to_parsed(entry: dict[str, Any]) -> ParsedReference:
    """Structured BibTeX fields → ParsedReference, with no regex guessing.

    ``.bib`` entries are already machine-readable; routing them through the
    raw-string heuristics (as a first implementation did) mangled authors like
    ``Kowalski, Jan`` into a title fragment. Exactness is free here — take it.
    """
    authors = [
        _normalise_bib_author(author)
        for author in str(entry.get("author") or "").split(" and ")
        if author.strip()
    ]
    year_digits = re.search(r"\d{4}", str(entry.get("year") or ""))
    venue = str(
        entry.get("journal")
        or entry.get("booktitle")
        or entry.get("publisher")
        or entry.get("school")
        or entry.get("institution")
        or ""
    ).strip()
    url = str(entry.get("url") or "").strip() or None
    if url is None and entry.get("eprint"):
        url = f"https://arxiv.org/abs/{entry['eprint']}"
    return ParsedReference(
        authors=authors,
        title=str(entry.get("title") or "").strip() or None,
        year=int(year_digits.group(0)) if year_digits else None,
        venue=venue or None,
        doi=str(entry.get("doi") or "").strip() or None,
        url=url,
    )


def _normalise_bib_author(author: str) -> str:
    """``Kowalski, Jan`` → ``Jan Kowalski``; ``Jan Kowalski`` is left alone."""
    author = author.strip().strip("{}")
    if "," in author:
        last, _, first = author.partition(",")
        return f"{first.strip()} {last.strip()}".strip()
    return author


def _bibentry_to_raw(entry: dict[str, Any]) -> str:
    """Deterministic human-readable rendering of a parsed entry."""
    authors = str(entry.get("author") or "").replace(" and ", "; ")
    title = str(entry.get("title") or "")
    venue = str(entry.get("journal") or entry.get("booktitle") or entry.get("publisher") or "")
    year = str(entry.get("year") or "")
    doi = str(entry.get("doi") or "")
    parts = [part for part in (authors, title, venue, year, f"doi:{doi}" if doi else "") if part]
    return ". ".join(parts)
