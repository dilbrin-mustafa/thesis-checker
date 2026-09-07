# ADR 0002 — Ingestion parses source documents, never compiles them

Date: 2026-09-07 · Status: accepted

## Context

Phase 1 needs headings, sections, citations, references, fonts and floats out of
DOCX, LaTeX and PDF. For LaTeX the obvious route is to run a TeX engine and read
the resulting DVI/PDF/log: the layout is authoritative, cross-references are
resolved, and the bibliography is already formatted.

That route was rejected for three reasons. First, it means executing
arbitrary user-supplied LaTeX in a container that has a full TeX distribution —
`\write18`, `\openout`, and package-level code execution make that a
remote-code-execution surface even behind rlimits. Second, a TeX Live install is
multiple gigabytes, which alone kills the HF Spaces demo profile. Third, the
result would no longer map back to the author's source: a finding could not point
at the line the student wrote.

## Decision

Parsers read source documents and produce the frozen `DocumentIR`. They never
invoke `pdflatex`, `xelatex`, `latexmk`, or any external typesetter, and they
never shell out at all.

Consequences we accept rather than work around:

- **LaTeX page numbers are unknown**, so `pages` is `0` and the omission is
  recorded in `extra["latex"]`. Block locators carry line numbers and file names
  instead, which is what a finding needs anyway.
- **`\tableofcontents`, `\listoffigures` and `\ref` cannot be resolved**, so a
  rendered ToC is not represented in the IR. Rather than fabricate one, the
  parser emits a `parse_warning`.
- **PDF is the reverse problem**: layout exists, semantics do not. Headings are
  induced by font-size clustering and, when available, validated against the
  embedded ToC. This is heuristic and the accuracy report says so.

The same rule applies to the sandbox: it exists to contain *parsing* (zip bombs,
decompression, memory), not to make code execution acceptable.

## Consequences

- No TeX distribution in any image; the demo profile stays small.
- Every finding can be traced to a source line, which the UI needs (doc 02 §3).
- The PDF accuracy target (`F1 > 0.75`) carries real risk and must be measured
  honestly — see `research/results/ingestion.md`.
- A future "compile it" mode would need a hard isolation boundary (separate
  machine, no network, disposable) and is out of scope for this project.
