#!/usr/bin/env python3
"""Generate the synthetic ingestion corpus + annotations.

Writes six documents (two per format) plus ``.expected.json`` sidecars into an
output directory, ready for::

    python backend/cli.py evaluate-ingestion --corpus <dir>

Why synthetic: the real corpus lives in ``research/corpus/documents/`` and is
deliberately **not** in git (unpublished theses are personal data — see
``research/corpus/LICENSE-AND-CONSENT.md``). This script makes the ingestion
harness runnable by anyone who clones the repo, which is a prerequisite for the
evaluation chapter being reproducible by the reviewer.

The fixtures are built by ``backend/tests/factories.py`` so the *same* ground
truth is used by the test suite and by the accuracy report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from tests.factories import (  # noqa: E402  (path set above)
    Expected,
    build_docx,
    build_docx_manual,
    build_pdf,
    build_pdf_no_toc,
    write_latex_article,
    write_latex_project,
)

BUILDERS = (
    ("thesis-docx", "file", build_docx),
    ("manual-docx", "file", build_docx_manual),
    ("thesis-pdf", "file", build_pdf),
    ("notoc-pdf", "file", build_pdf_no_toc),
    ("report-latex", "project", write_latex_project),
    ("article-latex", "project", write_latex_article),
)


def _write_sidecar(out: Path, name: str, expected: Expected) -> None:
    payload = expected.to_dict()
    path = out / f"{name}.expected.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_corpus(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, kind, builder in BUILDERS:
        if kind == "file":
            extension = "pdf" if "pdf" in name else "docx"
            target = out / f"{name}.{extension}"
            expected = builder(target)  # type: ignore[operator]
            print(f"  {target.relative_to(out)}")
        else:
            target = out / name
            expected = builder(target)  # type: ignore[operator]
            print(f"  {target.relative_to(out)}/main.tex")
        _write_sidecar(out, name, expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "data" / "synthetic-corpus",
        help="output directory (default: data/synthetic-corpus, which is gitignored)",
    )
    args = parser.parse_args(argv)
    print(f"writing synthetic corpus to {args.out}")
    build_corpus(args.out)
    print("done — now run:")
    print(f"  python backend/cli.py evaluate-ingestion --corpus {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
