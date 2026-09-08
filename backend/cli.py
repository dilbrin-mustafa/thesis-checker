"""Research/ops CLI — batch mode for experiments (doc 01 §1.3, persona: researcher).

Phase 0 shipped ``export-schema``. Phase 1 adds the two commands the ingestion
work is measured with:

* ``parse`` — one document in, canonical IR out (plus a one-line summary).
* ``evaluate-ingestion`` — a corpus directory in, per-format accuracy out.

``analyse`` still lands with the vertical slice (Phase 2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ingestion import metrics
from app.ingestion.enrich import enrichment_summary
from app.ingestion.errors import IngestionError
from app.ingestion.registry import detect_format, parse_document
from app.models.finding import Finding
from app.models.ir import DocumentIR, SourceFormat

CORPUS_MAIN_NAMES = ("main.tex", "master.tex", "thesis.tex", "praca.tex")


def export_schema(out: Path) -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ThesisGuard DocumentIR",
        "ir": DocumentIR.model_json_schema(),
        "finding": Finding.model_json_schema(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")


def cmd_parse(args: argparse.Namespace) -> int:
    path: Path = args.path
    requested: SourceFormat | None = args.format  # choices restrict this at runtime
    try:
        ir, seconds = metrics.time_parse(
            lambda: parse_document(
                path,
                fmt=requested,
                sandbox=not args.no_sandbox,
            )
        )
    except IngestionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(ir.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.out}")
    print(f"{path.name}: parsed in {seconds:.2f}s — {enrichment_summary(ir)}")
    return 0


def _corpus_entries(corpus: Path) -> list[tuple[str, Path, dict[str, Any]]]:
    """``<name>.<ext>`` + ``<name>.expected.json``; LaTeX projects are directories."""
    entries: list[tuple[str, Path, dict[str, Any]]] = []
    for sidecar in sorted(corpus.glob("*.expected.json")):
        name = sidecar.name[: -len(".expected.json")]
        annotation = json.loads(sidecar.read_text(encoding="utf-8"))
        candidates = [
            corpus / f"{name}.docx",
            corpus / f"{name}.pdf",
            corpus / f"{name}.tex",
        ]
        document = next((c for c in candidates if c.is_file()), None)
        if document is None and (corpus / name).is_dir():
            document = next(
                (
                    corpus / name / main
                    for main in CORPUS_MAIN_NAMES
                    if (corpus / name / main).is_file()
                ),
                None,
            )
        if document is None:
            print(f"skipping {name}: no document found", file=sys.stderr)
            continue
        entries.append((name, document, annotation))
    return entries


def cmd_evaluate(args: argparse.Namespace) -> int:
    corpus: Path = args.corpus
    entries = _corpus_entries(corpus)
    if not entries:
        print(f"no annotated documents in {corpus}", file=sys.stderr)
        return 1

    reports = []
    for name, document, annotation in entries:
        try:
            ir, seconds = metrics.time_parse(
                lambda d=document: parse_document(d, sandbox=not args.no_sandbox)
            )
        except IngestionError as exc:
            print(f"{name}: FAILED — {exc}", file=sys.stderr)
            continue
        report = metrics.evaluate(ir, annotation, document=name, runtime_s=seconds)
        reports.append(report)
        print(
            f"{name}: headings F1={report.headings.f1} sections F1={report.sections.f1} "
            f"citations F1={report.citations.f1} references={report.reference_accuracy} "
            f"lang={'ok' if report.language_correct else report.language_found} "
            f"({report.runtime_s}s)"
        )

    summary = metrics.summarise(reports)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out_json:
        metrics.write_json(args.out_json, reports)
        print(f"wrote {args.out_json}")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    try:
        print(detect_format(args.path))
    except IngestionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="thesisguard", description="ThesisGuard CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_schema = sub.add_parser("export-schema", help="Export IR+Finding JSON schema")
    p_schema.add_argument("--out", type=Path, required=True)

    p_parse = sub.add_parser("parse", help="Parse one document into the canonical IR")
    p_parse.add_argument("path", type=Path)
    p_parse.add_argument("--out", type=Path, default=None, help="write the IR JSON here")
    p_parse.add_argument(
        "--format",
        type=str,
        default=None,
        choices=["docx", "pdf", "latex"],
        help="skip magic-byte detection",
    )
    p_parse.add_argument(
        "--no-sandbox",
        action="store_true",
        help="parse in-process (trusted corpus only; skips the rlimit worker)",
    )

    p_eval = sub.add_parser(
        "evaluate-ingestion", help="Score parsers against annotated corpus documents"
    )
    p_eval.add_argument("--corpus", type=Path, required=True)
    p_eval.add_argument("--out-json", type=Path, default=None)
    p_eval.add_argument("--no-sandbox", action="store_true")

    p_detect = sub.add_parser("detect", help="Print the detected format of a file")
    p_detect.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "export-schema":
        export_schema(args.out)
        return 0
    if args.cmd == "parse":
        return cmd_parse(args)
    if args.cmd == "evaluate-ingestion":
        return cmd_evaluate(args)
    if args.cmd == "detect":
        return cmd_detect(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
