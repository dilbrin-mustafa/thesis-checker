from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

import pytest

# Ensure `import app...` resolves to backend/app without installation.
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.ir import DocumentIR  # noqa: E402
from tests.factories import (  # noqa: E402
    Expected,
    build_docx,
    build_docx_manual,
    build_pdf,
    build_pdf_no_toc,
    write_latex_article,
    write_latex_project,
)


class CorpusEntry(TypedDict):
    path: Path
    expected: Expected


@pytest.fixture(scope="session")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, CorpusEntry]:
    """All six synthetic documents, built once per session.

    ``expected`` holds the ground-truth annotations, so parser tests and the
    accuracy harness score against the same numbers.
    """
    root = tmp_path_factory.mktemp("corpus")
    docs: dict[str, CorpusEntry] = {}
    docs["docx"] = {"path": root / "thesis.docx", "expected": build_docx(root / "thesis.docx")}
    docs["docx_manual"] = {
        "path": root / "manual.docx",
        "expected": build_docx_manual(root / "manual.docx"),
    }
    docs["pdf"] = {"path": root / "thesis.pdf", "expected": build_pdf(root / "thesis.pdf")}
    docs["pdf_no_toc"] = {
        "path": root / "notoc.pdf",
        "expected": build_pdf_no_toc(root / "notoc.pdf"),
    }
    project = root / "report"
    docs["latex"] = {"path": project / "main.tex", "expected": write_latex_project(project)}
    article = root / "article"
    docs["latex_article"] = {
        "path": article / "main.tex",
        "expected": write_latex_article(article),
    }
    return docs


@pytest.fixture(scope="session")
def parsed(corpus: dict[str, CorpusEntry]) -> dict[str, DocumentIR]:
    """Enriched IR for every corpus document (session-scoped: parsing is the
    slow part of the suite)."""
    from app.ingestion.registry import parse_document

    return {key: parse_document(entry["path"], sandbox=False) for key, entry in corpus.items()}
