# 05 — Full Implementation Plan, Phase by Phase

Companion to docs 01–04. This is the execution document: what you build each week, in what order, and the concrete condition that says you may move on.

**Horizon:** 38 weeks, part-time (assume ~15–20 h/week alongside coursework).
**Governing rule:** every phase ends with something that *runs* and something that is *measured*. No phase ends with "the code is written".

---

## Phase discipline

Three rules that decide whether this project ships.

1. **The week-11 vertical slice is sacred.** Upload → analyse → report in a browser, end to end, even with only two modules. Everything after that is improvement to a working system rather than integration of four unfinished ones. If you are behind, cut module scope, never the slice date.
2. **Measure at the end of every phase.** The evaluation chapter is the graded part. Writing the harness in week 34 means fabricating retrospective numbers; writing it as you go means the chapter assembles itself.
3. **Modules are independently cuttable.** If Phase 5 overruns, you ship four modules and a shorter AIGT study. If it goes well, you have a publishable benchmark. Keep that optionality — never let a module become load-bearing for another.

**Weekly cadence:** Monday plan (3 concrete outcomes), Friday demo-to-yourself (run the thing end to end), Sunday 10-line log entry in `docs/journal.md`. That journal becomes your methodology chapter and saves days of reconstruction in month 8.

---

## Phase 0 — Foundations (weeks 1–3)

**Goal:** the skeleton runs, the corpus exists, the contract is frozen.

### Week 1 — Repository and infrastructure
- Repo per doc 01 §6; `pyproject.toml`, ruff + mypy strict + pytest configured.
- `docker-compose.yml`: FastAPI, Postgres+pgvector, Redis, LanguageTool.
- GitHub Actions: lint → typecheck → test → `pip-audit` → `gitleaks`.
- `make up`, `make test`, `make fmt`.
- `/health` endpoint. Deploy nothing yet.

### Week 2 — The corpus (do not skip or defer)
- Collect **20–30 real theses**: PDF, DOCX, LaTeX; Polish and English; ideally PG and ideally your faculty.
  - Sources: PG MOST Wiedzy repository, faculty pages, your supervisor, colleagues (with permission), other PL universities' open repositories.
- Obtain the **official PG `.docx` template** and ZR 49/2014 + faculty addenda. These are the literal ground truth for the format rules.
- Hand-annotate 5 of them: section boundaries, heading levels, citation spans, reference list. This is your ingestion test set. It takes a full day and pays for itself ten times.
- Store under `research/corpus/` with a `LICENSE-AND-CONSENT.md` recording provenance for each document.

> Every hour spent here saves five in Phase 1. Parser assumptions made without real documents are wrong in ways you cannot anticipate — real theses contain rotated pages, embedded Visio diagrams, mixed-language tables, and hand-broken equations.

### Week 3 — Freeze the contract
- `DocumentIR` as Pydantic models + a published JSON Schema.
- `Finding` model per doc 01 §3.3.
- Golden fixture: one hand-written IR instance that validates.
- Contract test all three parsers will later have to pass.
- Start the literature review — 20 papers on plagiarism detection, AIGT detection, and academic-writing tools. Set up Zotero now.

**Exit criteria**
- [ ] `make up` brings up all services; CI green
- [ ] ≥ 20 theses collected, 5 annotated
- [ ] PG template and regulations in hand
- [ ] IR schema frozen, versioned, and tested
- [ ] 20 papers in Zotero with notes

**Risk:** corpus collection stalls on permissions. Start emailing in week 1; work with open-repository documents while waiting.

---

## Phase 1 — Ingestion (weeks 4–7)

**Goal:** all three formats produce valid, *measured* IR.

### Week 4 — DOCX (start here)
Richest metadata, least ambiguity, and it validates the IR design before you fight PDF.
- `python-docx` + direct `w:` XML for true font/size/spacing/indent.
- Blocks, styles, headings from style names, captions, tables, footnotes.
- Security: `defusedxml`, zip-bomb ratio caps, member-count caps (doc 02 §3).

### Week 5 — LaTeX
- `pylatexenc` walker: `\section*`, `\cite`, `\ref`, `\label`, `\caption`, environments.
- Parse `.bib` (`bibtexparser`) and `.bbl`.
- **Never invoke a TeX engine.** Text-only parsing; reject `\input` outside the upload root.
- Multi-file projects: resolve includes within the sandbox, cap depth.

### Week 6 — PDF (timeboxed — one week, hard)
- PyMuPDF: spans with font, size, bbox, page.
- Heading induction: cluster by size/weight → levels; **validate against the extracted table of contents** (the single highest-leverage trick — the ToC tells you the ground-truth structure).
- `pdfplumber` for tables; caption detection by proximity + "Rys./Fig./Tab." prefix.
- Accept imperfection. Measure it, record it, move on.

> This is the classic sink. When the week is up, stop, write down the accuracy, and continue. You can return in Phase 6 if time allows — you almost certainly won't need to, because DOCX and LaTeX cover most users.

### Week 7 — Enrichment and measurement
- Language detection with `lingua-py`, per section.
- Section classification: bilingual keyword lexicon → canonical kinds, positional fallback.
- **Exclusion mask** (doc 01 §3.2) — front matter, ToC, bibliography, appendices, code, quotes.
- Sentence segmentation → `segments` table. Use a PL-aware splitter; naive `.` splitting breaks on "prof.", "rys.", "tzn.".
- Reference extraction: GROBID if you can run it, else CRF/regex.
- **Ingestion accuracy report**: heading F1, section F1, citation F1, reference-parse accuracy, per format.

**Exit criteria**
- [ ] Three parsers pass the IR contract test
- [ ] All 20+ corpus documents parse without crashing
- [ ] Accuracy measured per format, written to `research/results/ingestion.md`
- [ ] Exclusion masks verified by eye on 5 documents
- [ ] Parsing runs sandboxed with resource limits

**Target:** DOCX/LaTeX section F1 > 0.90; PDF > 0.75. If PDF is below 0.6, say so in the thesis and constrain the product claim — an honest measured limitation is fine; an unmeasured one is not.

---

## Phase 2 — Format, Grammar, and the Vertical Slice (weeks 8–11)

**The most important phase in the project.**

### Week 8 — Rule engine
- YAML rule loader + evaluator (doc 01 §4.3): predicates `assert`, `assert_not`, `assert_sections_present`, `assert_all_referenced`, `assert_page_range`, with tolerances and severities.
- Block-scope and document-scope rules.
- Bilingual messages; each rule links to its ZR 49/2014 clause.
- Every rule ships with a positive and a negative fixture.

### Week 9 — PG ruleset
- Encode the full checklist from doc 01 §4.3 (~30–40 rules).
- Per-format applicability matrix (font rules are meaningless for LaTeX; assert the document class instead).
- **Eval harness:** take compliant documents, inject known violations programmatically, measure per-rule precision/recall and the FP rate on clean documents.

### Week 10 — Grammar and style
- LanguageTool via Docker, block-by-block, exclusion mask applied, noisy categories disabled.
- Polish typography checks: orphan single-letter words, „quotes", półpauza, non-breaking spaces, decimal comma.
- Academic register (rule-based first, LLM later): first-person detection (PG requires impersonal form) — high precision, high value.
- Tune hard for precision. Group findings by rule so the UI shows "23 instances of X", not 23 rows.

### Week 11 — **THE VERTICAL SLICE**
- FastAPI: `POST /documents`, `POST /analyses`, `GET /analyses/{id}`, SSE progress.
- Minimal orchestrator (doc 03 §6) with parse → enrich → format → grammar → aggregate → report.
- HTML report via Jinja + WeasyPrint → PDF.
- Minimal frontend: upload, progress, findings grouped by severity, source snippet per finding.
- Deploy the CPU-only Docker Space. It doesn't need to be pretty; it needs to exist.

**Exit criteria**
- [ ] Upload a real thesis in a browser and get a real report
- [ ] Format rules measured: per-rule P/R, FP rate on clean docs
- [ ] Grammar precision measured on a sample
- [ ] Public URL your promoter can open
- [ ] End-to-end runtime under 2 minutes for an 80-page DOCX

**Show your promoter this week.** A working demo at the one-third mark changes the entire relationship for the rest of the project, and their feedback is far more useful now than in month 8.

---

## Phase 3 — Citation Verification (weeks 12–15)

Cheap, deterministic, high-value, great demo. A natural confidence-builder after the slice.

### Week 12 — Resolution
- Clients for Crossref, OpenAlex, DataCite, arXiv, PubMed, with polite pools, backoff, and a shared rate limiter.
- Cascading resolver: DOI exact → title+author fuzzy → title-only with a confidence threshold.
- Content-addressed cache (doc 03 §8), 90-day TTL.
- **SSRF protection on every outbound fetch** (doc 02 §5) — this module fetches user-controlled URLs, so it is your main SSRF exposure.

### Week 13 — Integrity and accuracy
- Orphan citations, uncited references, duplicates, numbering order.
- Field-level metadata diff (authors, year, venue, pages).
- Unresolvable → *possible fabricated reference*, phrased as a request to verify, never an accusation.
- Retraction check via Crossref `update-to`.

### Week 14 — Style and health
- Infer the dominant reference style; flag deviations.
- Link health (HEAD, cached, timeout-bounded).
- Source profile: age distribution, DOI coverage, self-citation ratio.

### Week 15 — Evaluation
- Build a 500-reference eval set with programmatically injected errors (wrong year, wrong author, mangled title) plus a fabricated-reference set (LLM-generated plausible fakes — a nicely topical touch).
- Measure resolution rate and error-detection P/R; write to `research/results/citations.md`.

**Exit criteria**
- [ ] ≥ 85% resolution on real theses (unresolvable are mostly PL-language books and standards — expected, document it)
- [ ] Fabricated-reference detection measured
- [ ] Cache hit rate > 60% on the second run
- [ ] SSRF tests pass
- [ ] Integrated into the report

---

## Phase 4 — Similarity (weeks 16–20)

### Week 16 — L1 internal duplication
- Sentence shingling → MinHash + LSH (`datasketch`) → cross-chapter duplicate clusters.
- Fully offline, private, fast. Ships immediately and is the only similarity layer the demo will use.

### Week 17 — Embeddings and vector store
- `multilingual-e5-base`; batch encode; store in pgvector (HNSW).
- `VectorIndex` abstraction with pgvector / brute-force NumPy implementations (doc 03 §2.1).
- Segment-level embedding cache — the big performance win for re-runs.

### Week 18 — L2 local corpus
- Corpus ingestion pipeline: OpenAlex/arXiv/CORE harvest by the thesis's topic + everything it cites.
- Two-stage retrieval: MinHash candidates → embedding rerank → passage alignment (Smith-Waterman over sentence similarities).
- **Enforce the `corpus_never_user_upload` constraint** (doc 03 §2) and test it.

### Week 19 — L3 web + paraphrase
- Fingerprint-sentence selection (rarest n-grams, ~5% of sentences).
- Web search provider behind an interface, hard per-document budget, aggressive caching.
- **Paraphrase signal:** high semantic / low lexical similarity → reported as its own category.
- Common-phrase suppression by IDF (doc 02 §10).

### Week 20 — Evaluation
- PAN plagiarism corpora: PlagDet, granularity, precision, recall.
- Synthetic PL paraphrase set (back-translation + paraphrase models) to test the paraphrase layer specifically.
- PRP1/PRP2-analogous coefficient with a stated definition.
- **FP rate on clean theses** — the number that matters most.

**Exit criteria**
- [ ] Three layers working; each independently toggleable
- [ ] PlagDet reported on PAN
- [ ] Paraphrase detection measured against the synthetic set
- [ ] FP rate on clean documents measured and low
- [ ] Every match shows its source side by side in the UI

---

## Phase 5 — AI Detection: the Research Core (weeks 21–27)

Everything before this was engineering. This is where the novelty budget goes. Protect these seven weeks.

### Week 21 — Dataset design and human class
- **Human:** open-access PL + EN theses/papers, **strictly pre-2021** to avoid contamination. Extract methodology and discussion sections — matched register is essential, or you end up training a genre classifier.
- Target ≥ 2 000 segments per language per class.
- Document the collection protocol properly; this is a thesis section.

### Week 22 — AI and adversarial classes
- **AI:** ≥ 3 generators (a GPT-class model, Bielik, Llama) × ≥ 3 prompt styles (naive, "write like a thesis chapter", few-shot conditioned on a real human section). The few-shot condition is the realistic hard case.
- **Adversarial:** paraphrased/"humanised" AI text — back-translation (PL→EN→PL), paraphrase models, a commercial humaniser if accessible.
- **Mixed:** human text with AI-edited sentences — the most common real behaviour and the hardest case.
- Stratified splits with **no topic leakage** between train and test.

### Week 23 — Zero-shot baselines
- Perplexity + burstiness (Bielik-7B for PL, an EN LM for EN).
- Fast-DetectGPT.
- Binoculars (two-model cross-perplexity; strong training-free baseline).
- Stylometric feature baseline — interpretable, and good thesis material.

### Week 24 — Supervised models
- Fine-tune XLM-RoBERTa and HerBERT.
- Train EN-only, PL-only, and joint, so you can measure transfer.
- Log everything with Weights & Biases or MLflow; seed everything.

### Week 25 — The experiments (this is N1)
1. In-language performance, PL vs EN.
2. **Cross-lingual transfer:** EN-trained → PL-tested. The core measurement.
3. Robustness: clean vs paraphrased vs mixed.
4. Generalisation to an unseen generator.
5. **Non-native-speaker bias:** PL authors' English vs native English — the fairness result from doc 02 §11, and arguably the most interesting finding you can produce.
6. Calibration (ECE, reliability diagrams).

Primary metric **TPR@1%FPR**, with AUROC secondary. Report false positives everywhere.

### Week 26 — Distillation and production integration
- Distil/quantise the best model for CPU inference (ONNX int8) — this is the demo's fallback path.
- ZeroGPU path per doc 04 §2A: module-scope model, `@spaces.GPU` with dynamic duration, chunked to stay under the cap, CPU fallback on quota exhaustion.
- Product guardrails (doc 02 §9): 150-word minimum, calibrated bands not point estimates, mandatory limitations panel, excluded from the aggregate score.

### Week 27 — Write it up while it's fresh
- Results tables generated by script, not by hand — you will re-run these.
- Draft the AIGT chapter now. It is the hardest chapter and the most valuable; do not defer it to week 36.

**Exit criteria**
- [ ] Dataset built, documented, and (if licensing allows) published on the HF Hub — a genuine artefact contribution
- [ ] 5+ methods compared on identical splits
- [ ] Cross-lingual transfer quantified
- [ ] Non-native-speaker bias measured
- [ ] Every experiment reproducible via one script
- [ ] Chapter drafted

**If you overrun:** cut the stylometric baseline and the unseen-generator experiment. Never cut the cross-lingual transfer study or the bias analysis — those are the contribution.

---

## Phase 6 — Aggregation, UX, Deployment (weeks 28–31)

### Week 28 — Aggregation and scoring
- Finding deduplication across modules (overlapping spans + related rules).
- Per-axis subscores: Originality, Compliance, Language, Citations.
- **AIGT reported separately, never folded in.**
- Readiness score with provisional weights — refitted in Phase 7 against reviewer data.

### Week 29 — The dashboard
- Findings list: filter by severity/module, group by rule, dismiss with reason (right of reply).
- **Source highlighting** — PDF.js for PDF, rendered HTML for DOCX/LaTeX, driven by the IR offsets. This is what makes the tool feel real.
- **"Top 10 fixes"** screen — the one students will actually use. Prioritise by severity × effort.
- Full PL/EN localisation; the accusatory-language CI test must pass for both.

### Week 30 — Reports and polish
- PDF report: summary page, per-axis sections, evidence appendix, engine + ruleset version stamp.
- JSON export for programmatic use.
- Progressive results over SSE — findings appear as modules land.
- Empty/error/partial states designed, not accidental.

### Week 31 — Deployment
- Ship the demo per doc 04: Gradio + ZeroGPU (option A), or Docker + separate inference Space (option D) if time allows.
- All demo guardrails active and tested (doc 04 §6).
- Measure cold start; bake weights; verify GPU budget ≤ ~30 s per analysis.
- **Record the fallback screencast.**

**Exit criteria**
- [ ] Public demo, cold start < 90 s, GPU budget verified
- [ ] Source highlighting works for all three formats
- [ ] PDF report generates correctly
- [ ] Guardrail test suite green
- [ ] 3 external users have run it and given feedback

---

## Phase 7 — Evaluation and Writing (weeks 32–38)

### Week 32 — The human study (arrange this in month 1, run it now)
- 30–50 theses rated by supervisors/reviewers on formal quality, language, citation quality, overall readiness.
- Compare the readiness score against human ratings: Spearman ρ, per-axis and overall.
- **Refit the aggregation weights** on this data with cross-validation. This is claim N3 and it converts an invented formula into a validated one.

### Week 33 — Usability study
- 8–12 students: think-aloud, task completion, SUS.
- Measure: do they find real problems? do they act on findings? do they trust the output? do they misread AIGT scores as verdicts? (that last one is a genuine finding either way)

### Week 34 — Ablations and system evaluation
- Which modules contribute most to the correlation with human judgement?
- End-to-end runtime, cache effectiveness, cost per analysis.
- Failure analysis: catalogue the 20 worst false positives and explain each. Reviewers love this section and it demonstrates real understanding.

### Weeks 35–37 — Writing
Suggested structure:
1. Introduction — problem, PG context, objectives, contributions
2. Related work — plagiarism detection, AIGT detection, grammar/style tools, citation verification, **JSA and the Polish context**
3. Requirements and scope — including what it explicitly is not (doc 01 §1.2)
4. System architecture — IR, pipeline, modules
5. AI-text detection study — the research core (drafted in week 27)
6. Implementation — stack, deployment, security and privacy engineering
7. Evaluation — per module, then system, then human study
8. Ethics, fairness, and limitations — a full chapter, not an appendix
9. Conclusions and future work

### Week 38 — Defence preparation
- 15-minute talk; live demo with the screencast as backup.
- Rehearse the hard questions:
  - *"How is this different from JSA?"* → doc 01 §1.2, and you scoped it deliberately.
  - *"AI detection is unreliable — why include it?"* → because you measured exactly how unreliable, including the bias against non-native speakers, and constrained the product accordingly. That is the finding.
  - *"Could a student use this to evade detection?"* → dual-use discussion; you report similarity to the author only, provide no rewriting, and log nothing to the institution.
  - *"Why should anyone trust the numbers?"* → measured FP rates, published methodology, versioned reports.

**Exit criteria**
- [ ] Human study complete, weights refitted and validated
- [ ] Usability study complete
- [ ] All results reproducible from scripts
- [ ] Thesis submitted
- [ ] Repo public with a README that lets someone else run it

---

## Cut list (in order)

When you fall behind — and you will — cut in this order. Decide by the *end* of Phase 4 whether Phase 5 gets its full seven weeks.

1. L3 web similarity (keep L1 + L2)
2. Citation context-support / NLI check
3. LLM academic-style layer (keep rule-based register checks)
4. PDF parsing beyond basic extraction
5. Stylometric AIGT baseline
6. Usability study (keep the human correlation study — it's a thesis claim)
7. ZeroGPU integration (CPU demo is perfectly acceptable)

**Never cut:** the vertical slice, the exclusion mask, per-phase measurement, the guardrail tests, the cross-lingual AIGT study.

---

## Standing definition of done

A phase is complete only when all of these hold:

- [ ] Code merged, CI green, type-checked
- [ ] Unit + integration tests for new paths
- [ ] Measured on real data; numbers written to `research/results/`
- [ ] Guardrail tests still pass (doc 02 §13)
- [ ] Performance budget still met (doc 03 §9)
- [ ] `docs/journal.md` updated
- [ ] Demo still runs end to end

That last item is the one people skip. Check it every single phase — an integration that silently broke in week 18 is a bad thing to discover in week 31.
