"""Parser interface (Phase 1 will implement DocxParser, LatexParser, PdfParser).

Phase 0 only freezes the contract: every parser MUST return a valid
``DocumentIR`` and MUST pass ``test_ir_contract``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.models.ir import DocumentIR


class BaseParser(ABC):
    format: str

    @abstractmethod
    def parse(self, path: Path, doc_id: str, filename: str) -> DocumentIR:
        """Parse *path* into a canonical DocumentIR. Must not raise on
        malformed-but-tolerable input — record what you can, flag the rest."""
        raise NotImplementedError
