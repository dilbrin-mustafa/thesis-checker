"""IR assembly helpers shared by all three parsers.

The invariant that makes highlighting possible (doc 01 §3.2) is enforced here
once instead of in every parser: ``plain_text[block.span.start:block.span.end]``
must always equal ``block.text``. :class:`IRBuilder` is the only supported way
to append blocks — it owns the offsets.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from app.models.ir import (
    Block,
    BlockStyle,
    BlockType,
    DocumentIR,
    Language,
    Layout,
    Locator,
    Source,
    SourceFormat,
    Span,
)

#: Blocks separated by a single newline in the normalised plain-text rendering.
BLOCK_SEPARATOR = "\n"


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """Streamed content hash — never loads the whole upload into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def count_words(text: str) -> int:
    return len(text.split())


def normalise_ws(text: str) -> str:
    """Collapse runs of whitespace *inside* a block (never across blocks).

    Parsers see tabs, soft line breaks and doubled spaces; downstream regexes
    (citations, reference parsing) and the UI do not want them.
    """
    return " ".join(text.split())


class IRBuilder:
    """Offset-tracking builder. Parsers push blocks; ``build()`` emits the IR.

    Order matters: blocks must be appended in reading order, because the
    exclusion mask and section ranges are derived from those offsets.
    """

    def __init__(
        self,
        *,
        doc_id: str,
        source_format: SourceFormat,
        filename: str,
        sha256: str,
    ) -> None:
        self.doc_id = doc_id
        self.source_format = source_format
        self.filename = filename
        self.sha256 = sha256
        self.blocks: list[Block] = []
        self._chunks: list[str] = []
        self._offset = 0
        self._seq = 0

    # -- construction ----------------------------------------------------
    def next_id(self, prefix: str = "b") -> str:
        self._seq += 1
        return f"{prefix}{self._seq:04d}"

    @property
    def plain_text(self) -> str:
        return BLOCK_SEPARATOR.join(self._chunks)

    def add(
        self,
        *,
        type: BlockType,
        text: str,
        level: int | None = None,
        locator: Locator | None = None,
        style: BlockStyle | None = None,
        flags: Sequence[str] = (),
    ) -> Block:
        """Append one block and return it with its span already resolved."""
        start = self._offset
        self._chunks.append(text)
        self._offset += len(text) + len(BLOCK_SEPARATOR)
        block = Block(
            id=self.next_id(),
            type=type,
            level=level,
            text=text,
            span=Span(start=start, end=start + len(text)),
            locator=locator or Locator(),
            style=style or BlockStyle(),
            flags=list(flags),
        )
        self.blocks.append(block)
        return block

    # -- finalisation ----------------------------------------------------
    def build(
        self,
        *,
        pages: int = 0,
        language: Language | None = None,
        layout: Layout | None = None,
        extra: dict[str, object] | None = None,
    ) -> DocumentIR:
        plain_text = self.plain_text
        return DocumentIR(
            doc_id=self.doc_id,
            source=Source(
                format=self.source_format,
                filename=self.filename,
                sha256=self.sha256,
                pages=pages,
                words=count_words(plain_text),
            ),
            language=language or Language(primary="pl"),
            plain_text=plain_text,
            blocks=self.blocks,
            layout=layout or Layout(),
            extra=dict(extra or {}),
        )


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
