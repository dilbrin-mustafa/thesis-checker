"""Security helpers (doc 02 §2-§5).

Phase 0: filename sanitisation + magic-byte validation scaffolding.
Full hardening (sandboxing, SSRF guards, consent gate) lands with the
modules that need it (Phase 1 upload/parse, Phase 3 citations).
"""

from __future__ import annotations

import uuid
from pathlib import Path

ALLOWED_EXTENSIONS = {"pdf", "docx", "tex"}
ALLOWED_MAGIC = {
    "pdf": (b"%PDF-",),
    "docx": (b"PK\x03\x04",),  # zip container; structural checks in Phase 1
    "tex": (),  # plain text — sniffed as text, see is_probably_text()
}


def sanitised_storage_name(original_filename: str) -> str:
    """Discard the client-supplied name entirely (doc 02 §3 step 5).

    Returns ``{uuid}.{ext}`` — eliminates path traversal and
    Unicode-homoglyph tricks in one move.
    """
    ext = Path(original_filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file extension: {ext!r}")
    return f"{uuid.uuid4().hex}.{ext}"


def sniff_format(head: bytes) -> str | None:
    """Return 'pdf' | 'docx' | 'tex' | None from the first bytes of a file."""
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "docx"
    if is_probably_text(head):
        return "tex"
    return None


def is_probably_text(sample: bytes) -> bool:
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        # Try latin-2 / windows-1250 — common in older Polish documents.
        try:
            sample.decode("cp1250")
        except UnicodeDecodeError:
            return False
    return True


def validate_extension_matches_content(filename: str, head: bytes) -> str:
    """Enforce extension <-> magic-byte agreement (doc 02 §3 steps 2-3)."""
    ext = Path(filename).suffix.lower().lstrip(".")
    sniffed = sniff_format(head)
    if sniffed is None:
        raise ValueError("Unrecognised file content (magic-byte sniffing failed)")
    # .tex uploads are plain text; accept any text content for them.
    if ext == "tex" and sniffed == "tex":
        return sniffed
    if ext != sniffed:
        raise ValueError(f"Extension .{ext} does not match detected content {sniffed!r}")
    return sniffed
