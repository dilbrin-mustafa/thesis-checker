# 01 — Complete Blueprint

**Project:** Automatic Thesis Checking System (working name: **ThesisGuard**)
**Context:** MSc thesis, Gdańsk University of Technology (PG). Must be both a defensible research artefact and a working tool.
**Inputs:** PDF, DOCX, LaTeX · **Languages:** Polish + English
**Interface:** Web app — upload → dashboard → downloadable report
**Deployment target:** Hugging Face Spaces free tier for the public demo; Docker Compose for the full local system.

---

## 1. Product definition

### 1.1 What it is

A **pre-submission advisory system** that a student or supervisor runs on a draft thesis and receives a structured, evidence-linked quality report across four axes: originality, formal compliance & language, AI-generated-text likelihood, and citation integrity.

### 1.2 What it explicitly is *not*

| Not | Why this matters |
|---|---|
| A JSA replacement | JSA (Jednolity System Antyplagiatowy) is the only legally mandated PL system, is promoter-only, has no public API, and uniquely holds ORPPD. Any claim of equivalence is indefensible. |
| An accusation engine | No output is ever a verdict. Every finding is a *signal with confidence and evidence*, for a human to adjudicate. |
| A grading system | It reports compliance and risk, not quality of the research. |

Write both of these into the thesis Scope chapter. Pre-empting the reviewer's objection is worth more than hoping they miss it.

### 1.3 Primary users

| Persona | Need | Key screen |
|---|---|---|
| **Student (primary)** | "Is my draft submittable? What must I fix?" | Prioritised fix-list with source highlighting |
| **Supervisor (secondary)** | "Where should I focus my review time?" | Summary + risk flags + exportable PDF |
| **Researcher (you)** | Reproducible experiments, ablations | CLI + JSON output + batch mode |

---

## 2. Research framing

Four features glued together is a competent tool but a thin MSc. The contribution is the **integration plus evaluation**, anchored by three claims:

| ID | Claim | Evidence you will produce |
|---|---|---|
| **N1** | AI-generated-text detection degrades measurably from English to Polish academic prose, and can be partly recovered by multilingual fine-tuning | A PL/EN academic AIGT benchmark + cross-lingual transfer study with TPR@1%FPR |
| **N2** | Institutional editorial requirements (PG ZR 49/2014) can be encoded as a declarative rule DSL and checked automatically with high precision | Rule DSL + per-rule P/R on synthetically corrupted documents |
| **N3** | Multi-signal aggregation into a calibrated readiness score correlates with human reviewer judgement better than any single signal | Correlation study on 30–50 theses with reviewer ratings |

**Thesis title candidate:**
*"Multi-signal automated quality assessment of bilingual (Polish/English) engineering theses: architecture, cross-lingual AI-text detection, and empirical validation"*

---

## 3. System architecture

### 3.1 Logical view

```
┌────────────────────────────────────────────────────────────────┐
│                        Presentation                            │
│   React SPA · dashboard · source-highlighting · PDF export     │
└───────────────────────────┬────────────────────────────────────┘
                            │ REST + SSE (progress)
┌───────────────────────────▼────────────────────────────────────┐
│                       API (FastAPI)                            │
│   auth · upload · job submit · status · results · report       │
└───────────────────────────┬────────────────────────────────────┘
                            │ enqueue
┌───────────────────────────▼────────────────────────────────────┐
│                     Orchestrator (job runner)                  │
│   DAG execution · retries · timeouts · partial results         │
└───────────────────────────┬────────────────────────────────────┘
                            │
        ┌───────────────────▼────────────────────┐
        │            INGESTION LAYER             │
        │   PdfParser · DocxParser · LatexParser │
        │        → Canonical Document IR         │
        │   language detect · exclusion masks    │
        └───────────────────┬────────────────────┘
                            │ IR (single contract)
   ┌──────────┬─────────────┼──────────────┬──────────────┐
   ▼          ▼             ▼              ▼              ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌────────────┐ ┌────────────┐
│Similar-│ │Format  │ │ Grammar  │ │    AIGT    │ │  Citation  │
│  ity   │ │ Rules  │ │ & Style  │ │ Detection  │ │Verification│
└───┬────┘ └───┬────┘ └────┬─────┘ └─────┬──────┘ └─────┬──────┘
    └──────────┴───────────┴─────────────┴──────────────┘
                            │ Finding[]
        ┌───────────────────▼────────────────────┐
        │      AGGREGATION & REPORTING           │
        │  dedup · severity · readiness score    │
        │  HTML / PDF / JSON renderers           │
        └────────────────────────────────────────┘
```

### 3.2 The one contract that matters: Canonical Document IR

Every parser produces it; every analyser consumes only it. **Analysers never touch the original file.** This is what makes the system testable, format-agnostic, and swappable.

Non-negotiable property: **every element carries a character offset range into a normalised plain-text rendering, plus a source locator** (page+bbox for PDF, XML path for DOCX, line number for LaTeX). Without this you cannot highlight findings in the UI, and the product dies.

```jsonc
{
  "schema_version": "1.0",
  "doc_id": "uuid",
  "source": { "format": "docx|pdf|latex", "filename": "...", "sha256": "...",
              "pages": 87, "words": 18400 },
  "language": { "primary": "pl", "per_section": { "abstract_en": "en" } },
  "plain_text": "…full normalised text…",

  "blocks": [{
    "id": "b0142",
    "type": "heading|paragraph|caption|list_item|table|figure|equation|code|footnote|bibitem|quote",
    "level": 2,
    "text": "Metodyka badań",
    "span": { "start": 10432, "end": 10446 },
    "locator": { "page": 14, "bbox": [72, 340, 460, 358] },
    "style": { "font": "Times New Roman", "size_pt": 12, "bold": true,
               "italic": false, "line_spacing": 1.5, "align": "left",
               "indent_cm": 0.0, "space_before_pt": 12 },
    "flags": ["excluded_from_analysis"]
  }],

  "sections": [{ "id": "s03", "kind": "introduction", "title": "Wstęp",
                 "block_ids": ["b0031","b0032"], "page_range": [7, 11] }],

  "citations": [{ "id": "c031", "raw": "[12]", "style": "numeric",
                  "block_id": "b0142", "span": {...}, "ref_id": "r012" }],

  "references": [{ "id": "r012", "raw": "Kowalski J.: …",
                   "parsed": { "authors": [...], "title": "...", "year": 2021,
                               "venue": "...", "doi": "10.xxxx/yyy", "url": null },
                   "confidence": 0.92 }],

  "figures":   [{ "id": "f07", "number": "3.2", "caption": "...", "page": 31,
                  "referenced_by": ["b0210"] }],
  "tables":    [{ "id": "t04", "number": "2.1", "caption": "...", "page": 22,
                  "referenced_by": [] }],
  "equations": [{ "id": "e11", "number": "(4.3)", "page": 44 }],

  "layout": { "margins_cm": { "top": 2.5, "bottom": 2.5, "inner": 3.5, "outer": 2.0 },
              "page_size": "A4", "numbering_starts_at": 4 },

  "exclusion_mask": [{ "start": 0, "end": 3200, "reason": "front_matter" },
                     { "start": 91000, "end": 104000, "reason": "bibliography" }]
}
```

**The exclusion mask is the single highest-leverage design decision in the system.** Title page, statement of authorship, bilingual abstracts, table of contents, bibliography, appendices, code listings, and marked quotations must be excluded from plagiarism, grammar, and AIGT analysis. Skipping this produces a report that is 80% false positives and destroys user trust in the first thirty seconds.

### 3.3 Universal finding object

Every analyser emits the same shape. The aggregator and UI then need no per-module logic.

```jsonc
{
  "id": "uuid",
  "module": "format|grammar|similarity|aigt|citation",
  "rule_id": "PG.FONT.BODY",
  "severity": "blocker|error|warning|info",
  "confidence": 0.87,
  "title_pl": "Nieprawidłowa czcionka tekstu głównego",
  "title_en": "Incorrect body-text font",
  "detail_pl": "Wykryto Calibri 11 pkt; wymagane Times New Roman 12 pkt.",
  "detail_en": "...",
  "locations": [{ "block_id": "b0142", "span": {...}, "locator": {...} }],
  "evidence": { /* module-specific: matched source URL, LT match, resolved DOI … */ },
  "suggested_fix": { "type": "replace|manual", "text": "..." },
  "docs_url": "/rules/PG.FONT.BODY"
}
```

---

## 4. Module specifications

### 4.1 Ingestion

| Format | Primary tool | Fallback | Fidelity |
|---|---|---|---|
| DOCX | `python-docx` + direct `w:` XML for styles | — | **Highest.** True font/size/spacing/indent. Treat as ground truth for format rules. |
| LaTeX | `pylatexenc` (walker) + `.bib`/`.bbl` | `TexSoup` | **Highest semantics.** `\section`, `\cite`, `\ref`, `\label` are explicit. Format rules apply only to the document class/template, not fonts. |
| PDF | `PyMuPDF` (spans, fonts, bboxes) | `pdfplumber` (tables), Tesseract `pol+eng` (scanned) | **Lowest, most work.** Font/size recoverable; logical structure must be inferred. |

Shared post-parse steps:
1. **Structure inference** (PDF only) — cluster spans by font size/weight to induce heading levels; validate against the extracted table of contents.
2. **Language detection** — `lingua-py` (far better than `langdetect` on short PL text), per section, because abstracts are deliberately bilingual.
3. **Section classification** — map headings to canonical kinds (`introduction`, `literature_review`, `methodology`, `results`, `summary`, `bibliography`) via a bilingual keyword lexicon + fallback positional heuristics.
4. **Exclusion mask construction.**
5. **Reference parsing** — `anystyle`-style CRF or GROBID if available; regex+heuristic fallback.

> **Scheduling warning:** PDF structure inference is the classic hidden time sink in this kind of project. Budget for it explicitly, ship DOCX and LaTeX first, and treat PDF as best-effort *with measured accuracy* rather than an assumed capability.

### 4.2 Similarity / plagiarism

Three independent layers; they catch genuinely different things.

**L1 — Internal duplication (offline, ships day one).**
Sentence shingling → MinHash + LSH (`datasketch`) → cross-chapter duplicate clusters. Catches copy-paste between chapters and recycled paragraphs. Cheap, private, no external dependency, and a real finding students care about.

**L2 — Local corpus.**
User-supplied reference PDFs plus a corpus you assemble (open PG/other theses, papers cited by the thesis). Two stage: MinHash candidate retrieval → sentence-level embedding similarity (`multilingual-e5-base` or LaBSE) → passage alignment via Smith-Waterman over sentence-similarity scores.

**L3 — Web / open repositories.**
Crossref, OpenAlex, arXiv, CORE (free API key), plus a metered general web search. Cost control is mandatory: select only **fingerprint sentences** (highest-rarity n-grams, ~5% of sentences), cache aggressively by content hash, and enforce a hard per-document query budget.

**Paraphrase signal (sub-contribution).** Where embedding similarity is high but lexical (Jaccard) similarity is low, you have candidate paraphrase. Report it as a distinct category — it is exactly what naive tools miss and what reviewers ask about.

Output a **PRP1/PRP2-analogous coefficient** mirroring JSA's definitions, so the numbers are immediately interpretable to a PG promoter.

### 4.3 Format compliance — rules as code

Encode PG ZR 49/2014 (plus your faculty's addenda) as a declarative YAML rule set evaluated by a small engine. This is claim N2, and it is what makes the module a contribution rather than a pile of `if` statements.

```yaml
- id: PG.FONT.BODY
  title_pl: "Czcionka tekstu głównego"
  applies_to: { block_type: [paragraph], source_format: [docx, pdf] }
  assert: { font: "Times New Roman", size_pt: 12 }
  tolerance: { size_pt: 0.5 }
  severity: error

- id: PG.SPACING.BODY
  applies_to: { block_type: [paragraph] }
  assert: { line_spacing: 1.5 }
  severity: error

- id: PG.HEAD.NO_TRAILING_DOT
  applies_to: { block_type: [heading] }
  assert_not: { text_matches: '\.\s*$' }
  severity: warning

- id: PG.STRUCT.REQUIRED
  scope: document
  assert_sections_present:
    [title_page, statement, abstract_pl, abstract_en, toc,
     introduction, summary, bibliography]
  severity: blocker

- id: PG.FIG.REFERENCED
  scope: document
  assert_all_referenced: figures
  message_pl: "Rysunek {number} nie jest przywołany w tekście."
  severity: error

- id: PG.LEN.MSC
  scope: document
  assert_page_range: [60, 100]
  severity: info
```

Coverage checklist: margins (2.5 top/bottom; mirrored 3.5 inner / 2.0 outer), A4, 1.5 line spacing, TNR 12 pt body, heading depth ≤ 3, heading font sizes per level, no trailing period in headings, 1.5 cm first-line indent, page numbering visible from page 3/4 in the outer footer, figure captions *below* / table captions *above*, caption font 10 pt, numbering continuity per chapter, every figure/table/equation referenced in text, foreign terms in italics, abstract ≤ 1 page, required bilingual abstracts + keywords, bibliography contains only cited works, consistent reference style.

Each rule ships with: machine-readable definition, a bilingual human message, a link to the exact clause in ZR 49/2014, and a unit test with a positive and negative fixture.

### 4.4 Grammar & academic style

**Backbone:** self-hosted **LanguageTool** (Docker). Best-in-class free Polish support, offline, no per-request cost. Feed it block-by-block with the exclusion mask applied, and disable rule categories that misfire on academic text (e.g. whitespace rules inside equations).

**Layer 2 — academic register (LLM, optional).** Checks LanguageTool structurally cannot do:
- Use of first person — PG guidance requires impersonal form (*"wykonano"*, not *"wykonałem"*). High-value, high-precision, easy win.
- Tense consistency within sections; hedging and overclaiming; sentence-length distribution; passive-voice overuse; terminology consistency (same concept, different words).

**Layer 3 — Polish typography.** Cheap deterministic checks with real payoff:
- Orphan single-letter words (`w`, `i`, `z`, `a`, `o`) at line ends — a standard PL editorial requirement, needs a non-breaking space.
- Polish quotation marks „…" vs "…".
- Dash usage: półpauza vs hyphen; spacing around them.
- Non-breaking space before units and between number and unit.
- Decimal separator consistency (comma in PL text).

**Precision is the whole game here.** A grammar checker that flags 400 things is ignored. Tune for precision, group findings by rule, and let the user expand rather than drown.

### 4.5 AI-generated-text detection — the research core

Treat everything else as engineering; this is where the novelty budget goes.

**Methods to implement and compare:**

| Family | Method | Notes |
|---|---|---|
| Zero-shot statistical | Perplexity + burstiness | Baseline; needs a PL LM (Bielik-7B / PLLuM) and an EN LM |
| Zero-shot curvature | Fast-DetectGPT | Strong, no training data |
| Zero-shot cross-perplexity | **Binoculars** | Best current training-free method; two-model ratio |
| Supervised | Fine-tuned **XLM-RoBERTa** / **HerBERT** | Your main PL contribution |
| Stylometric | Feature-based (burstiness, TTR, function-word dist.) | Interpretable baseline; good for the thesis |

**Dataset construction (this *is* N1):**
- **Human class:** open-access PL + EN theses and papers, **pre-2021** to avoid contamination. Extract methodology/discussion sections specifically — matched register matters.
- **AI class:** generate matched-topic sections with ≥3 generators (a GPT-class model, Bielik, Llama) × ≥3 prompt styles (naive, "write like a thesis", few-shot conditioned on the human section).
- **Adversarial class:** paraphrased/"humanised" AI text (back-translation, paraphrase models, commercial humanisers). This is the realistic attack and where honest results get interesting.
- **Mixed class:** human text with AI-edited sentences — the most common real behaviour, and the hardest case.

**Metrics:** AUROC, **TPR@1%FPR** (the metric that matters, because false accusation is the harm), cross-lingual transfer delta (EN-trained → PL-tested), robustness delta under paraphrase, and calibration (ECE + reliability diagram).

**Product-side rules, non-negotiable:**
1. Never render a binary verdict. Output a calibrated probability band per section.
2. Always show the confidence interval and the phrase "this is a statistical signal, not evidence".
3. Suppress output entirely for sections below a minimum length (~150 words) — short-text detection is noise.
4. Dedicate a thesis section to the ethics and the false-accusation risk. Reviewers will ask; having thought about it first is a differentiator.

### 4.6 Citation verification

The most underrated feature in the set: cheap, deterministic, highly demo-friendly, and topical (hallucinated references).

| Check | Method | Output |
|---|---|---|
| **Existence** | Resolve via Crossref → OpenAlex → DataCite → arXiv → PubMed, fuzzy title+author matching | Unresolvable ⇒ *possible fabricated reference* (warning, never accusation) |
| **Metadata accuracy** | Compare parsed vs resolved authors/year/venue/pages | Field-level diff |
| **Integrity** | Set operations on citations ↔ references | Orphan citations; uncited references; duplicates; numbering order |
| **Style consistency** | Infer dominant pattern, flag outliers | "Entry 34 deviates from the dominant style" |
| **Retractions** | Crossref `update-to` / Retraction Watch | Blocker if cited uncritically |
| **Link health** | HEAD requests, cached | Dead URL; missing access date |
| **Source profile** | Age distribution, self-citation ratio, venue quality heuristic, % with DOI | Advisory metrics |
| **Context support (stretch)** | NLI or LLM: does the cited abstract support the citing sentence? | "Weak support" — flag, with an explicit reliability caveat |

Caching is essential: a content-addressed cache keyed by normalised reference string, with a long TTL. Most theses cite the same canonical works.

### 4.7 Aggregation & reporting

1. **Deduplicate** overlapping findings across modules (same span + similar rule → merge).
2. **Severity ladder:** `blocker` (will be rejected) → `error` (must fix) → `warning` (should fix) → `info`.
3. **Per-axis subscores** (0–100): Originality, Formal Compliance, Language, Citation Integrity, plus AI-likelihood reported **separately and never folded into the total** — mixing a probabilistic, contested signal into a compliance score is indefensible.
4. **Readiness score:** weighted aggregate, with weights *fitted and validated* against human reviewer ratings (claim N3), not invented.
5. **Outputs:** interactive dashboard (findings linked to highlighted source), printable PDF report, machine-readable JSON, and a "top 10 fixes" quick list — the screen students will actually use.

---

## 5. Technology stack

| Layer | Full system | Demo (HF Spaces) |
|---|---|---|
| API | FastAPI (Python 3.12) | same |
| Jobs | Celery + Redis | in-process `asyncio` worker + SQLite queue |
| DB | PostgreSQL 16 + pgvector | SQLite + `sqlite-vec` |
| Blobs | MinIO / S3 | ephemeral `/tmp`, auto-purged |
| Frontend | React + TS + Vite + Tailwind + shadcn/ui | same, built to static, served by FastAPI |
| Grammar | LanguageTool server (Docker) | LanguageTool `--jvm` in-container, or public API with rate limit |
| Embeddings | `multilingual-e5-base` | `multilingual-e5-small` (quantised, ONNX) |
| AIGT | Fine-tuned XLM-R + Binoculars | distilled/quantised classifier only |
| LLM | Ollama local, or OpenAI/Anthropic opt-in | HF Inference API, opt-in, key from Space secrets |
| Reports | WeasyPrint | same |
| Quality | pytest, ruff, mypy, GitHub Actions | same |

**One codebase, two profiles.** Select via `APP_PROFILE=full|demo`. Do *not* fork the code for the demo — the divergence will bite you two weeks before the deadline.

---

## 6. Repository layout

```
thesis-checker/
├── docs/                      # these documents + ADRs
├── rules/pg/                  # YAML rule sets (ZR 49/2014)
├── backend/
│  ├── app/
│  │  ├── api/                 # FastAPI routers
│  │  ├── core/                # config, security, profiles
│  │  ├── ingestion/           # pdf.py docx.py latex.py ir.py
│  │  ├── analysis/
│  │  │  ├── similarity/  format/  grammar/  aigt/  citation/
│  │  ├── aggregation/         # dedup, scoring, report
│  │  ├── orchestrator/        # DAG runner
│  │  ├── storage/             # repositories (DB-agnostic)
│  │  └── models/              # SQLAlchemy + Pydantic
│  ├── tests/{unit,integration,fixtures}/
│  └── cli.py                  # batch mode for experiments
├── frontend/
├── research/                  # notebooks, dataset builders, experiments
│  ├── datasets/  experiments/  results/
├── deploy/
│  ├── docker-compose.yml
│  └── hf-space/               # Dockerfile + README.md header for Spaces
└── Makefile
```

Keep `research/` in the same repo but **out of the runtime import path**. Reproducibility of experiments is graded; entanglement with production code is punished.

---

## 7. Roadmap

| Phase | Weeks | Deliverable | Done when |
|---|---|---|---|
| **0 Foundations** | 1–3 | Repo, CI, Compose, IR schema frozen, 20–30 thesis corpus collected, lit review started | `make up` works; schema has tests |
| **1 Ingestion** | 4–7 | 3 parsers → IR, language detect, exclusion masks | Section-F1 measured on 20 annotated theses |
| **2 Format + Grammar** | 8–11 | Rule engine + PG ruleset + LanguageTool | **Vertical slice: upload → HTML report in the browser** |
| **3 Citations** | 12–15 | Resolvers, integrity, accuracy, hallucination detection | 500-reference eval set passes |
| **4 Similarity** | 16–20 | L1 internal, L2 corpus, L3 web | PAN corpus PlagDet reported |
| **5 AIGT (research)** | 21–27 | Dataset, 5 methods, cross-lingual + adversarial study | Results tables generated by script |
| **6 Aggregation + UX** | 28–31 | Scoring, dashboard, PDF report, **HF demo live** | Public Space URL works from a cold start |
| **7 Evaluation + writing** | 32–38 | Reviewer study, ablations, thesis text | Defence |

**The single most important milestone is week 11.** An end-to-end vertical slice — even with only two modules — converts this from four risky research projects into one working system you are incrementally improving. Protect that date over any feature.

---

## 8. Evaluation plan

| Module | Data | Primary metrics |
|---|---|---|
| Ingestion | 20–30 real theses, hand-annotated | Heading/section F1, citation extraction F1, reference-parse accuracy |
| Format | Compliant docs with programmatically injected violations | Per-rule P/R; **FP rate on clean documents** |
| Grammar | PL/EN annotated academic sentences + injected errors | Precision@suggestion; FP rate |
| Similarity | PAN corpora + synthetic PL paraphrase set | PlagDet, granularity, precision/recall |
| AIGT | Your PL/EN benchmark + adversarial split | AUROC, **TPR@1%FPR**, cross-lingual Δ, ECE |
| Citations | 500 refs with injected errors + fabricated set | Resolution rate, error-detection P/R |
| System | 30–50 theses with supervisor ratings | Spearman ρ (readiness vs human), SUS, task-completion time |

Report **false-positive rate everywhere and prominently**. Make "precision over recall" an explicit, stated design principle in the thesis — it is a defensible engineering position and it pre-empts the obvious criticism of tools in this space.

---

## 9. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PDF structure inference consumes months | High | High | DOCX/LaTeX first; PDF best-effort with measured accuracy; hard timebox |
| No JSA/ORPPD access | Certain | Medium | Reframed as pre-submission advisory in scope; discussed in related work |
| AIGT is inherently unreliable | Certain | Medium | Make unreliability the *finding*; report TPR@1%FPR; never a verdict |
| Scope creep across 4 modules | High | High | Independently shippable modules; week-11 vertical slice; features can be cut |
| External API cost | Medium | Low | Fingerprint-sentence selection, content-hash caching, hard budget cap |
| GDPR / unpublished-thesis confidentiality | Medium | High | Local-first default, opt-in external calls, retention policy, consent for the study (see doc 02) |
| HF free tier too small for real models | High | Medium | Demo profile: quantised models, page cap, no web layer (see doc 04) |
| Reviewer study can't recruit participants | Medium | High | Ask your promoter in **month 1**, not month 7 |

---

## 10. Immediate next actions

1. Obtain the **official PG `.docx` thesis template** and your faculty's addenda — these are the literal ground truth for the format rules, and everything in §4.3 depends on them.
2. Book a conversation with your promoter covering scope, the reviewer study (you need their time and possibly ethics sign-off), and whether the faculty can supply an anonymised thesis corpus.
3. Collect 20–30 real theses across all three formats. **You cannot design the IR from imagination** — every parser assumption you make without real inputs will be wrong.
4. Freeze the IR schema with tests, then build Phase 1.
