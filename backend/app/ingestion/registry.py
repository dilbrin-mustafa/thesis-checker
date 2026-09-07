"""Ingestion entry point: format detection → guards → parser → enrichment.

This is the only module the orchestrator (Phase 2) and the CLI should call.
Everything else in the package is an implementation detail behind
:func:`parse_document`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.security import validate_extension_matches_content
from app.ingestion.docx_parser import DocxParser
from app.ingestion.enrich import enrich
from app.ingestion.errors import ParseError, UnsupportedFormatError
from app.ingestion.latex_parser import LatexParser
from app.ingestion.pdf_parser import PdfParser
from app.ingestion.sandbox import parse_sandboxed
from app.models.ir import DocumentIR, SourceFormat

PARSERS: dict[SourceFormat, type[DocxParser] | type[LatexParser] | type[PdfParser]] = {
    "docx": DocxParser,
    "latex": LatexParser,
    "pdf": PdfParser,
}

_EXTENSION_TO_FORMAT: dict[str, SourceFormat] = {
    "docx": "docx",
    "pdf": "pdf",
    "tex": "latex",
    "latex": "latex",
}


def detect_format(path: Path) -> SourceFormat:
    """Sniff the format from magic bytes, cross-checked against the extension."""
    with path.open("rb") as handle:
        head = handle.read(4096)
    if not head:
        raise ParseError("file is empty")
    sniffed = validate_extension_matches_content(path.name, head)
    fmt = _EXTENSION_TO_FORMAT.get(sniffed)
    if fmt is None:
        raise UnsupportedFormatError(f"unsupported format: {sniffed}")
    return fmt


def parse_in_process(
    path: Path, fmt: SourceFormat, doc_id: str, filename: str, *, settings: Settings | None = None
) -> DocumentIR:
    """Run the parser and enrichment chain in the current process."""
    parser_cls = PARSERS.get(fmt)
    if parser_cls is None:
        raise UnsupportedFormatError(f"no parser for {fmt!r}")
    parser = parser_cls(settings or get_settings())
    ir = parser.parse(path, doc_id, filename)
    return enrich(ir)


def parse_document(
    path: Path,
    *,
    fmt: SourceFormat | None = None,
    doc_id: str | None = None,
    filename: str | None = None,
    sandbox: bool | None = None,
    settings: Settings | None = None,
) -> DocumentIR:
    """Parse *path* into an enriched DocumentIR.

    ``sandbox`` defaults to ``settings.parse_sandboxed``: on by default so the
    resource limits are always exercised, and switchable for tests and for the
    batch research CLI where the corpus is already trusted.
    """
    config = settings or get_settings()
    resolved_format = fmt or detect_format(path)
    resolved_id = doc_id or str(uuid.uuid4())
    resolved_name = filename or path.name

    use_sandbox = config.parse_sandboxed if sandbox is None else sandbox
    if not use_sandbox:
        return parse_in_process(path, resolved_format, resolved_id, resolved_name, settings=config)
    return parse_sandboxed(
        path,
        resolved_format,
        resolved_id,
        resolved_name,
        timeout_s=config.parse_timeout_s,
        memory_mb=config.parse_memory_mb,
    )
