# Automatic Thesis Checking System — Project Plan

**Context:** MSc thesis project @ Gdańsk University of Technology (PG), must also work as a real tool.
**Inputs:** PDF, DOCX, LaTeX · **Languages:** English + Polish
**Delivery:** Web app (upload → dashboard → downloadable PDF report)
**Budget stance:** hybrid — free/open by default, paid APIs only where unavoidable

---

## 0. Framing: what makes this a *thesis*, not just a tool

A tool alone won't defend. The defensible research contribution should be:

> **A multi-signal, bilingual (PL/EN) automated pre-submission quality assessment pipeline for engineering theses, with a study of cross-lingual transfer in AI-generated-text detection and an empirical evaluation of an aggregated "thesis readiness score" against human reviewer judgements.**

Three concrete novelty claims you can actually defend:

| # | Claim | Why it's novel |
|---|---|---|
| N1 | Polish-language AI-text detection benchmark for academic writing | Nearly all AIGT detectors are EN-trained; PL academic register is under-studied. You can build a dataset + measure cross-lingual degradation. |
| N2 | Structure/format compliance checking driven by a machine-readable encoding of PG's ZR 49/2014 editorial rules | Rule-as-code for a specific institutional standard; generalisable rule DSL. |
| N3 | Aggregated readiness score validated against supervisor/reviewer scores | Turns 4 independent signals into one calibrated, evaluated output. |

**Honest scoping note (important):** JSA (Jednolity System Antyplagiatowy) is the only official PL system, is promoter-only, has no public API, and students cannot access it. Your system must be positioned as a **pre-submission advisory tool**, explicitly *not* a JSA replacement. Say this in the thesis — it turns a limitation into a scoping decision.

---

## 1. Architecture

```
                    ┌──────────────────────────┐
   PDF/DOCX/TEX ───▶│  Ingestion & Normalisation│
                    │  → Canonical Document IR  │
                    └────────────┬──────────────┘
                                 │  (blocks, spans, offsets, metadata)
        ┌────────────────┬───────┴────────┬──────────────────┐
        ▼                ▼                ▼                  ▼
  ┌───────────┐   ┌────────────┐   ┌────────────┐    ┌──────────────┐
  │Similarity │   │  Format &  │   │    AIGT    │    │   Citation   │
  │/Plagiarism│   │  Grammar   │   │ Detection  │    │ Verification │
  └─────┬─────┘   └─────┬──────┘   └─────┬──────┘    └──────┬───────┘
        └───────────────┴────────────────┴──────────────────┘
                                 ▼
                      ┌──────────────────────┐
                      │  Aggregator / Score  │
                      │  + Report Generator  │
                      └──────────┬───────────┘
                                 ▼
                   Web dashboard + PDF/HTML report
```

**Key design decision: the Canonical Document IR.** Everything downstream depends on it. One JSON schema, produced by three different parsers, with *character offsets preserved back to the source* so every finding can be highlighted in the original.

```jsonc
{
  "doc_id": "...", "language": "pl", "source_format": "docx",
  "blocks": [
    { "id": "b12", "type": "heading|paragraph|caption|table|figure|equation|code|footnote|bibitem",
      "level": 2, "text": "...", "page": 14,
      "style": { "font": "Times New Roman", "size_pt": 12, "bold": false,
                 "line_spacing": 1.5, "align": "justify", "indent_cm": 1.5 },
      "span": { "start": 10432, "end": 10981 } }
  ],
  "sections": [ { "title": "Wstęp", "kind": "introduction", "block_ids": [...] } ],
  "citations": [ { "id": "c3", "raw": "[12]", "block_id": "b12", "target": "r12" } ],
  "references": [ { "id": "r12", "raw": "...", "parsed": { "doi": "...", "authors": [], "year": 2021 } } ],
  "figures": [...], "tables": [...], "equations": [...]
}
```

---

## 2. Module-by-module

### 2.1 Ingestion

| Format | Tooling | Notes |
|---|---|---|
| DOCX | `python-docx` + raw XML | **Richest source** — real style info (font, size, spacing, indent). Use as ground truth for format checks. |
| LaTeX | `pylatexenc` / `TexSoup`, plus `.bbl`/`.bib` | Semantics are explicit (`\section`, `\cite`, `\ref`). Format rules apply only partially — check the *class/template*, not fonts. |
| PDF | `PyMuPDF` (primary), `pdfplumber` (tables) | Recover font/size/position per span; `pypdf` for metadata. Fallback OCR (`tesseract` w/ `pol`+`eng`) only for scanned pages. |

Cross-cutting: language detection (`lingua-py` — much better than `langdetect` for PL/EN), per-section, because abstracts are bilingual.

**Deliberate exclusions from analysis:** title page, statement, bibliography, appendices, code listings, quoted blocks. Feed these as *exclusion masks* into every module — this single decision massively reduces false positives.

### 2.2 Plagiarism / Similarity

Three-layer approach (do all three, they catch different things):

1. **Internal duplication / self-plagiarism** — MinHash+LSH (`datasketch`) over shingled sentences. Catches copy-paste between chapters. Cheap, fully offline, works day one.
2. **Local corpus** — user-supplied reference PDFs + a crawled corpus of open PG/other theses. Two-stage: MinHash candidate generation → sentence-level embedding similarity (`sentence-transformers`, multilingual e5 / LaBSE) → alignment of matched passages.
3. **Web / open repositories** — Crossref, OpenAlex, arXiv, CORE (free API key), plus a metered web-search fallback (Bing/Brave/Serper) on *fingerprint sentences* only (rarest-n-gram selection), to keep cost bounded.

Also add **paraphrase detection**: embedding similarity ≫ lexical similarity is the signal. This is a nice sub-contribution.

Output: matched-span list with source, similarity, and a PRP-like coefficient (mirror JSA's PRP1/PRP2 definitions so numbers are interpretable to your promoter).

### 2.3 Format & Grammar

**Format = rules-as-code.** Encode PG ZR 49/2014 + your faculty's guidance as a YAML rule set:

```yaml
- id: PG.FONT.BODY
  applies_to: {block_type: paragraph, source_format: [docx, pdf]}
  assert: {font: "Times New Roman", size_pt: 12}
  severity: error
  message_pl: "Tekst główny: Times New Roman 12 pkt."
- id: PG.STRUCT.REQUIRED_SECTIONS
  assert_sections_present: [title_page, statement, abstract_pl, abstract_en,
                            toc, introduction, summary, bibliography]
- id: PG.LEN.MSC
  assert_page_range: [60, 100]
  severity: warning
```
Rules to cover: margins (2.5 top/bottom, mirror 3.5/2.0), 1.5 spacing, heading levels ≤3, no trailing period in headings, 1.5 cm paragraph indent, page numbering from p.4, figure/table caption placement & numbering continuity, every figure/table referenced in text, foreign terms in italics, abstract ≤1 page, bibliography style consistency.

**Grammar/style:** `LanguageTool` self-hosted (excellent PL support, free, Docker) as the backbone → plus an LLM pass for academic-register issues LT can't see (first person usage — PG requires impersonal form, tense consistency, hedging, sentence length, passive overuse). Custom checks: PL typography (orphan single-letter words `w`, `i`, `z` at line ends — a classic PL editorial rule), quote marks „ ", non-breaking spaces, dash usage.

### 2.4 AI-Generated Text Detection

This is your **research core** — treat the rest as engineering.

- **Baselines:** perplexity/burstiness via a PL LLM (Bielik-7B or PLLuM) and an EN one; DetectGPT/Fast-DetectGPT; Binoculars (strong, training-free).
- **Supervised:** fine-tune XLM-RoBERTa / HerBERT on a dataset you build.
- **Your dataset (N1):** collect open-access PL + EN theses/papers (pre-2021 = human), generate matched AI sections with several models (GPT-class, Bielik, Llama) and several prompting styles, plus **humanised/paraphrased** variants (the realistic adversarial case).
- **Evaluation:** AUROC, TPR@1%FPR (the metric that matters — false accusations are the harm), cross-lingual transfer EN→PL, robustness to paraphrasing, calibration.
- **Ethics/UX:** never output "this is AI". Output a calibrated probability with explicit uncertainty, per-section, and a prominent disclaimer. Put an ethics section in the thesis.

### 2.5 Citation Verification

Genuinely underserved and very demo-friendly. Checks:

1. **Existence** — resolve each reference via Crossref / OpenAlex / DOI.org / arXiv / PubMed. Flag unresolvable → possible **hallucinated citation** (huge topical relevance).
2. **Metadata accuracy** — authors/year/venue/pages match the resolved record.
3. **Integrity** — every `\cite`/[n] has a bibliography entry and vice-versa (orphans & uncited refs); numbering order; duplicate entries.
4. **Style conformance** — consistent style across entries (detect the dominant pattern, flag deviations).
5. **Health signals** — retracted papers (Retraction Watch / Crossref), dead URLs, missing access dates, predatory-venue heuristic, % of sources older than N years, self-citation ratio.
6. **Stretch (nice contribution): citation-context support** — does the cited abstract actually support the claim in the sentence? NLI/LLM check. Great demo, honest about limits.

### 2.6 Aggregation & Report

Per-module scores → weighted **Thesis Readiness Score** with severity tiers (blocker / error / warning / info). Weights *derived*, not invented: collect human reviewer ratings on ~30–50 theses and fit/validate (that's your N3 evaluation chapter).

Report: interactive dashboard (inline source highlighting via offsets) + exportable PDF + JSON.

---

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12, **FastAPI** | ML ecosystem is Python; async + OpenAPI free |
| Async jobs | **Celery + Redis** (or arq) | Analyses take minutes; must not block HTTP |
| DB | **PostgreSQL** + **pgvector** | Documents, findings, and embeddings in one place |
| Object store | MinIO / local FS | Uploaded files |
| Frontend | **React + TypeScript + Vite + Tailwind + shadcn/ui** | PDF highlighting via `react-pdf`/PDF.js |
| ML | PyTorch, HuggingFace, sentence-transformers, datasketch | |
| Grammar | LanguageTool (Docker) | |
| LLM | Local Ollama (Bielik/Llama) + optional OpenAI/Anthropic | hybrid budget |
| Deploy | Docker Compose | Reproducible, promoter can run it |
| Quality | pytest, ruff, mypy, GitHub Actions | Shows engineering maturity |

**Privacy:** unpublished theses are sensitive. Default = fully local processing; any external API call must be opt-in, explicit, and logged. Document a data-flow diagram + retention policy in the thesis. This is a real evaluation criterion.

---

## 4. Roadmap (~7 months, part-time)

| Phase | Weeks | Output |
|---|---|---|
| **0. Foundations** | 1–3 | Repo, Docker Compose, CI, literature review started, ingestion IR schema frozen |
| **1. Ingestion** | 4–7 | All 3 parsers → IR, language detection, exclusion masks, parser accuracy eval on 20 real theses |
| **2. Format + Grammar** | 8–11 | YAML rule engine, PG ruleset, LanguageTool integration. **First end-to-end vertical slice: upload → HTML report.** |
| **3. Citations** | 12–15 | Crossref/OpenAlex resolvers w/ caching, integrity + accuracy + hallucination checks |
| **4. Plagiarism** | 16–20 | MinHash internal + local corpus + embedding paraphrase layer; web layer last |
| **5. AI detection (research)** | 21–27 | Dataset construction, baselines, fine-tuned XLM-R/HerBERT, cross-lingual + robustness experiments |
| **6. Aggregation & UX** | 28–31 | Readiness score, dashboard polish, PDF report |
| **7. Evaluation & writing** | 32–38 | Human-reviewer validation study, ablations, thesis text, defence |

**Milestone discipline:** finish the vertical slice in Phase 2. Having *something* that runs end-to-end by week 11 de-risks everything.

---

## 5. Evaluation plan (the chapter that gets you the grade)

| Module | Dataset | Metrics |
|---|---|---|
| Ingestion | 20–30 real theses (PDF/DOCX/TEX), hand-annotated structure | Section/heading F1, citation extraction F1 |
| Format | Synthetically corrupted compliant docs (inject known violations) | Precision/recall per rule, FP rate on clean docs |
| Grammar | PL/EN error-annotated academic sentences + injected errors | Precision @ suggestion, FP rate (critical) |
| Plagiarism | PAN plagiarism-detection corpora + synthetic PL paraphrase set | PlagDet, granularity, precision/recall |
| AI detection | Your PL/EN corpus + adversarial paraphrase set | AUROC, **TPR@1%FPR**, cross-lingual delta, calibration (ECE) |
| Citations | 500 refs w/ injected errors + hallucinated set | Resolution rate, error-detection P/R |
| System | 30–50 theses w/ supervisor ratings | Correlation of readiness score vs human; user study (SUS, task time) |

Always report **false-positive rate** prominently. An accusatory tool with poor precision is worse than none — make that an explicit design principle in the thesis.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| PDF parsing quality is the hidden time sink | Prioritise DOCX/LaTeX; treat PDF as best-effort and *measure* it |
| No access to JSA/ORPPD | Reframe as pre-submission advisory; be explicit in scope + related work |
| AI detection is unreliable on principle | Make unreliability the research finding; report TPR@1%FPR, never a verdict |
| Scope creep across 4 big features | Vertical slice by week 11; features are independently shippable |
| Web-search API cost | Fingerprint-sentence selection + aggressive caching + hard budget cap |
| Ethics / GDPR on thesis text | Local-first, opt-in external calls, retention policy, consent for the user study |

---

## 7. Immediate next steps

1. Confirm the **exact** PG faculty guidelines you'll encode (ZR 49/2014 + your faculty's; get the official `.docx` template — it's the ground truth for the format rules).
2. Talk to your promoter about scope and about the **evaluation study** (you'll need reviewer time and possibly ethics sign-off).
3. Collect a seed corpus of 20–30 theses in all three formats — you cannot design the IR without real inputs.
4. Freeze the IR schema, then build Phase 1.
