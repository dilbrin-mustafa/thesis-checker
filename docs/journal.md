# Engineering journal

Weekly cadence (doc 05): Monday plan (3 outcomes) → Friday demo-to-self →
Sunday 10-line log entry here. This becomes the methodology chapter.

## Week 1 — Foundations: repo + CI + IR contract
- Planned: ...
- Built: ...
- Measured: ...
- Next: ...

## Week 2 — Ingestion: DOCX → LaTeX → PDF + measurement harness
- Planned: three parsers producing the frozen IR, enrichment, and an honest
  measurement harness.
- Built: `backend/app/ingestion/` — `docx_parser`, `latex_parser`, `pdf_parser`,
  plus `builder`, `enrich`, `sections`, `sentences`, `language`, `citations`,
  `references`, `bibliography`, `exclusions`, `guards`, `sandbox`, `registry`,
  `metrics`. CLI gained `detect`, `parse`, `evaluate-ingestion`.
  - DOCX reads raw `w:` XML for true font/size/spacing/indent, falls back to
    manual-formatting inference when a template has no style metadata.
  - LaTeX is text-only (never invokes a TeX engine); `\input`/`\include` are
    resolved strictly inside the upload root, cycles are recorded not followed.
  - PDF induces headings by font-size clustering, validated against extracted ToC
    bookmarks; ligatures are expanded at extraction time so regexes match.
  - Security: `defusedxml` everywhere, zip-bomb ratio cap, member-count caps,
    absolute/`..` zip members rejected, sandboxed subprocess with rlimits.
- Measured: 6 synthetic documents (2 DOCX, 2 LaTeX, 2 PDF), all 1.00 on heading
  F1, section F1, citation F1, reference accuracy, language, figure/table recall.
  Mean runtime 0.21–0.27 s/doc. One expected warning (`\tableofcontents` cannot
  be rendered without a TeX engine). **These are regression anchors, not accuracy
  — the corpus is self-authored and `research/corpus/` is empty.** Details and
  the caveats: `research/results/ingestion.md`.
- Gate: 173 tests pass; `ruff check` clean; `ruff format --check` clean;
  `mypy --strict` clean over `backend/app` + `backend/tests` (53 files).
- Next: Phase 2 (format rules + grammar + vertical slice). Before that, drop any
  real theses into `research/corpus/` and re-run `evaluate-ingestion` — the
  harness needs no changes, only data.
