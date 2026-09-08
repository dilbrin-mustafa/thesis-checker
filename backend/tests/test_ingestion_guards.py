"""Upload/container guardrail tests (doc 02 §3 step 4 + format table).

Every check here is a control against a specific hostile document shape. They
must fail *closed* — the parser never sees the bytes if a guard trips.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ingestion.errors import ParseError, ResourceLimitError, SuspiciousArchiveError
from app.ingestion.guards import (
    check_docx_container,
    check_file_size,
    check_latex_project,
    resolve_include,
)

CONTENT_TYPES = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)
DOCUMENT_XML = (
    '<?xml version="1.0"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>ok</w:t></w:r></w:p></w:body></w:document>"
)


def _write_docx(
    path: Path, members: dict[str, bytes] | None = None, payload: bytes | None = None
) -> Path:
    parts: dict[str, bytes] = {
        "[Content_Types].xml": CONTENT_TYPES.encode(),
        "word/document.xml": DOCUMENT_XML.encode(),
    }
    parts.update(members or {})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data if data is not None else b"")
    if payload is not None:
        with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/media/payload.bin", payload)
    return path


def test_valid_container_passes(tmp_path: Path) -> None:
    path = _write_docx(tmp_path / "ok.docx")
    check_docx_container(path, Settings())


def test_zip_bomb_is_rejected_on_compression_ratio(tmp_path: Path) -> None:
    path = _write_docx(tmp_path / "bomb.docx", payload=b"\0" * (8 * 1024 * 1024))
    settings = Settings(max_compression_ratio=100)
    with pytest.raises(SuspiciousArchiveError):
        check_docx_container(path, settings)


def test_uncompressed_budget_is_enforced(tmp_path: Path) -> None:
    path = _write_docx(tmp_path / "big.docx", payload=b"a" * (2 * 1024 * 1024))
    settings = Settings(max_uncompressed_mb=1)
    with pytest.raises(SuspiciousArchiveError):
        check_docx_container(path, settings)


def test_member_count_is_capped(tmp_path: Path) -> None:
    members = {f"word/media/f{i}.bin": b"x" for i in range(20)}
    path = _write_docx(tmp_path / "many.docx", members=members)
    with pytest.raises(SuspiciousArchiveError):
        check_docx_container(path, Settings(max_zip_members=5))


def test_path_traversal_in_a_member_is_rejected(tmp_path: Path) -> None:
    path = _write_docx(tmp_path / "traversal.docx", members={"../evil.xml": b"<x/>"})
    with pytest.raises(SuspiciousArchiveError):
        check_docx_container(path, Settings())


def test_missing_document_part_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nodoc.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
    with pytest.raises(ParseError):
        check_docx_container(path, Settings())


def test_a_plain_zip_is_not_a_docx(tmp_path: Path) -> None:
    path = tmp_path / "plain.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(ParseError):
        check_docx_container(path, Settings())


def test_file_size_cap(tmp_path: Path) -> None:
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 2048)
    with pytest.raises(ResourceLimitError):
        check_file_size(path, Settings(max_upload_mb=0))
    assert check_file_size(path, Settings(max_upload_mb=1)) == 2048


def test_latex_project_file_cap(tmp_path: Path) -> None:
    for index in range(4):
        (tmp_path / f"chapter{index}.tex").write_text("tekst", encoding="utf-8")
    main = tmp_path / "main.tex"
    main.write_text("tekst", encoding="utf-8")
    with pytest.raises(ResourceLimitError):
        check_latex_project(main, tmp_path, Settings(max_latex_files=3))
    check_latex_project(main, tmp_path, Settings(max_latex_files=10))


def test_missing_main_file(tmp_path: Path) -> None:
    with pytest.raises(ParseError):
        check_latex_project(tmp_path / "nope.tex", tmp_path, Settings())


def test_include_resolution_stays_inside_the_root(tmp_path: Path) -> None:
    """The root is the upload directory — mirrors ``LatexParser.parse``."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "inside.tex").write_text("wewnątrz", encoding="utf-8")
    (tmp_path / "outside.tex").write_text("na zewnątrz", encoding="utf-8")
    main = project / "main.tex"
    main.write_text("", encoding="utf-8")

    assert resolve_include("inside", project, main) is not None
    assert resolve_include("inside.tex", project, main) is not None
    assert resolve_include("../outside", project, main) is None
    assert resolve_include("/etc/passwd", project, main) is None
    assert resolve_include("", project, main) is None
    assert resolve_include("missing", project, main) is None


def test_symlinked_include_outside_the_root_is_refused(tmp_path: Path) -> None:
    """A symlink is the sneaky version of path traversal."""
    outside = tmp_path / "secret.tex"
    outside.write_text("tajne", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    link = project / "sneaky.tex"
    try:
        link.symlink_to(outside)
    except OSError:  # pragma: no cover - filesystems without symlink support
        pytest.skip("symlinks unavailable")
    main = project / "main.tex"
    main.write_text("", encoding="utf-8")
    # the link resolves outside the project root → refused
    assert resolve_include("sneaky", project, main) is None
