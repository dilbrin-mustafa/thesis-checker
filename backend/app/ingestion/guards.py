"""Structural pre-flight guards (doc 02 §3 step 4 and the format table).

These run *before* any parser library touches the bytes: a hostile DOCX or PDF
must be rejected on shape, not on behaviour. Each check fails closed.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.core.config import Settings
from app.ingestion.errors import (
    EncryptedDocumentError,
    ParseError,
    ResourceLimitError,
    SuspiciousArchiveError,
)

DOCX_REQUIRED_PARTS = ("word/document.xml", "[Content_Types].xml")


def check_file_size(path: Path, settings: Settings) -> int:
    """Size cap before reading (doc 02 §3 step 1)."""
    size = path.stat().st_size
    limit = settings.max_upload_mb * 1024 * 1024
    if size > limit:
        raise ResourceLimitError(f"file is {size} bytes, cap is {limit} bytes")
    return size


def check_docx_container(path: Path, settings: Settings) -> None:
    """DOCX = zip: cap members, uncompressed total, ratio; reject traversal.

    Covers the three classic container attacks in one pass: zip bombs
    (ratio + total-extracted-bytes budget), path traversal in member names,
    and archives that are not actually WordprocessingML packages.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise ParseError(f"not a readable zip container: {exc}") from exc

    if len(infos) > settings.max_zip_members:
        raise SuspiciousArchiveError(f"{len(infos)} members, cap is {settings.max_zip_members}")

    names = {info.filename for info in infos}
    for missing in DOCX_REQUIRED_PARTS:
        if missing not in names:
            raise ParseError(f"container is missing {missing}")

    total_uncompressed = 0
    total_compressed = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or name.startswith("\\"):
            raise SuspiciousArchiveError(f"absolute path in archive: {name!r}")
        parts = Path(name).parts
        if ".." in parts:
            raise SuspiciousArchiveError(f"path traversal in archive: {name!r}")
        total_uncompressed += info.file_size
        total_compressed += info.compress_size

    limit = settings.max_uncompressed_mb * 1024 * 1024
    if total_uncompressed > limit:
        raise SuspiciousArchiveError(
            f"uncompressed size {total_uncompressed} exceeds budget {limit}"
        )
    if total_compressed > 0:
        ratio = total_uncompressed / total_compressed
        if ratio > settings.max_compression_ratio:
            raise SuspiciousArchiveError(
                f"compression ratio {ratio:.0f}:1 exceeds cap {settings.max_compression_ratio}:1"
            )


def check_pdf(path: Path, settings: Settings) -> int:
    """Page cap and encryption check. Returns the page count."""
    import pymupdf  # imported lazily: the PDF extra is optional

    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise EncryptedDocumentError("password-protected PDF")
            pages = int(doc.page_count)
    except EncryptedDocumentError:
        raise
    except Exception as exc:  # pymupdf raises many types; treat all as parse errors
        raise ParseError(f"unreadable PDF: {exc}") from exc

    if pages > settings.max_pages:
        raise ResourceLimitError(f"{pages} pages, cap is {settings.max_pages}")
    if pages == 0:
        raise ParseError("PDF contains no pages")
    return pages


def check_latex_project(path: Path, root: Path, settings: Settings) -> None:
    """Cap the number of .tex files a project may pull in (doc 02 §3)."""
    for count, _candidate in enumerate(root.rglob("*.tex"), start=1):
        if count > settings.max_latex_files:
            raise ResourceLimitError(
                f"more than {settings.max_latex_files} .tex files in the upload"
            )
    if not path.exists():
        raise ParseError(f"missing main file: {path.name}")


def resolve_include(name: str, root: Path, current: Path) -> Path | None:
    """Resolve a LaTeX ``\\input``/``\\include`` target inside the upload root.

    Returns ``None`` for anything that escapes the sandbox (absolute paths,
    ``..`` traversal, symlinks out of the root) — the caller records a warning
    instead of following it. ``\\write18`` is never reachable: we never invoke
    a TeX engine (doc 02 §3).
    """
    candidate = name.strip()
    if not candidate or candidate.startswith(("/", "\\")):
        return None
    if not candidate.endswith(".tex"):
        candidate += ".tex"
    base = (current.parent / candidate) if not Path(candidate).is_absolute() else Path(candidate)
    try:
        resolved = base.resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(root_resolved):
        return None
    return resolved if resolved.is_file() else None
