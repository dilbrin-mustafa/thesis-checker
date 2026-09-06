"""Upload-validation basics (doc 02 §3). Extended in Phase 1."""

from __future__ import annotations

import pytest

from app.core.security import (
    sanitised_storage_name,
    sniff_format,
    validate_extension_matches_content,
)


def test_storage_name_discards_client_name() -> None:
    out = sanitised_storage_name("../../../etc/passwd.pdf")
    assert out.endswith(".pdf") and "/" not in out and ".." not in out


def test_storage_name_rejects_bad_extension() -> None:
    with pytest.raises(ValueError):
        sanitised_storage_name("evil.exe")


def test_sniffing() -> None:
    assert sniff_format(b"%PDF-1.7 ...") == "pdf"
    assert sniff_format(b"PK\x03\x04....") == "docx"
    assert sniff_format("rozdział \\section{wstęp}".encode()) == "tex"
    assert sniff_format(b"\x00\x01\x02binary") is None


def test_extension_must_match_content() -> None:
    with pytest.raises(ValueError):
        validate_extension_matches_content("fake.pdf", b"PK\x03\x04....")
    assert validate_extension_matches_content("a.pdf", b"%PDF-1.4") == "pdf"
