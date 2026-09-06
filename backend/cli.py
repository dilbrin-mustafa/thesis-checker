"""Research/ops CLI — batch mode for experiments (doc 01 §1.3, persona: researcher).

Phase 0: schema export only. `analyse` lands with the vertical slice (Phase 2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models.finding import Finding
from app.models.ir import DocumentIR


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="thesisguard", description="ThesisGuard CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_schema = sub.add_parser("export-schema", help="Export IR+Finding JSON schema")
    p_schema.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "export-schema":
        export_schema(args.out)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
