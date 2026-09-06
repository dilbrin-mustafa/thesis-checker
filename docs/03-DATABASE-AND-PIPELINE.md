# 03 — Database & Pipeline

Covers the persistence model, the analysis pipeline (DAG, orchestration, failure semantics), and the caching strategy that makes repeat runs fast and external costs bounded.

---

## PART A — DATABASE

## 1. Design constraints

1. **Portability.** The same schema must run on PostgreSQL (full) and SQLite (HF demo). Consequence: no PG-only types in core tables. Use `TEXT` + JSON, `UUID` as `CHAR(36)`, and isolate vector search behind an interface (`pgvector` vs `sqlite-vec`).
2. **Findings are the product.** Model them as first-class, queryable rows — not a JSON blob — because the UI filters, sorts, and groups them constantly.
3. **Document text is sensitive.** It lives in one table with a short TTL, encrypted at rest in the full deployment.
4. **Everything is reproducible.** Every analysis records engine version, rule-set version, and model versions. Without this you cannot defend an experiment or explain a changed result.

## 2. Schema

```sql
-- ─── Identity ────────────────────────────────────────────────
CREATE TABLE users (
    id            UUID PRIMARY KEY,
    email         TEXT UNIQUE,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'student',   -- student|supervisor|admin
    locale        TEXT NOT NULL DEFAULT 'pl',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);

-- ─── Documents ───────────────────────────────────────────────
CREATE TABLE documents (
    id               UUID PRIMARY KEY,
    owner_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename         TEXT NOT NULL,              -- sanitised, display only
    storage_key      TEXT,                       -- NULL once the blob is purged
    source_format    TEXT NOT NULL,              -- pdf|docx|latex
    sha256           CHAR(64) NOT NULL,
    size_bytes       BIGINT NOT NULL,
    page_count       INT,
    word_count       INT,
    language_primary TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    purge_after      TIMESTAMPTZ NOT NULL,       -- retention clock
    deleted_at       TIMESTAMPTZ
);
CREATE INDEX idx_documents_owner ON documents(owner_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_documents_purge ON documents(purge_after) WHERE deleted_at IS NULL;

-- Parsed IR, split out so it can be purged independently of metadata.
CREATE TABLE document_ir (
    document_id   UUID PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    ir            JSONB NOT NULL,       -- blocks, sections, citations, layout
    plain_text    TEXT NOT NULL,        -- encrypted at rest (full profile)
    parser_version TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sentence-level units: the working granularity for similarity + AIGT.
CREATE TABLE segments (
    id           BIGSERIAL PRIMARY KEY,
    document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    block_id     TEXT NOT NULL,
    section_kind TEXT,
    ordinal      INT  NOT NULL,
    text         TEXT NOT NULL,
    char_start   INT  NOT NULL,
    char_end     INT  NOT NULL,
    lang         TEXT,
    excluded     BOOLEAN NOT NULL DEFAULT FALSE,
    exclude_reason TEXT,
    simhash      BIGINT,
    embedding    VECTOR(768)            -- pgvector; separate table on SQLite
);
CREATE INDEX idx_segments_doc ON segments(document_id, ordinal);
CREATE INDEX idx_segments_emb ON segments
       USING hnsw (embedding vector_cosine_ops) WHERE excluded = FALSE;

-- ─── Analyses ────────────────────────────────────────────────
CREATE TABLE analyses (
    id             UUID PRIMARY KEY,
    document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    owner_id       UUID NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
    status         TEXT NOT NULL,      -- queued|running|partial|completed|failed|cancelled
    profile        TEXT NOT NULL,      -- demo|full
    requested_modules TEXT[] NOT NULL,
    consent        JSONB NOT NULL,     -- which external calls were authorised
    engine_version TEXT NOT NULL,
    ruleset_version TEXT NOT NULL,
    model_versions JSONB NOT NULL,
    readiness_score NUMERIC(5,2),
    subscores      JSONB,              -- {originality, compliance, language, citations}
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_analyses_owner_created ON analyses(owner_id, created_at DESC);

-- Per-module execution record. Enables partial results and honest reporting.
CREATE TABLE module_runs (
    id            UUID PRIMARY KEY,
    analysis_id   UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    module        TEXT NOT NULL,       -- format|grammar|similarity|aigt|citation
    status        TEXT NOT NULL,       -- pending|running|ok|skipped|timeout|error
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    duration_ms   INT,
    finding_count INT DEFAULT 0,
    error_kind    TEXT,
    error_message TEXT,
    metrics       JSONB,               -- module-specific summary numbers
    UNIQUE (analysis_id, module)
);

-- ─── Findings: the product ───────────────────────────────────
CREATE TABLE findings (
    id             UUID PRIMARY KEY,
    analysis_id    UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    module         TEXT NOT NULL,
    rule_id        TEXT NOT NULL,
    severity       TEXT NOT NULL,      -- blocker|error|warning|info
    confidence     NUMERIC(4,3),
    title_pl       TEXT NOT NULL,
    title_en       TEXT NOT NULL,
    detail_pl      TEXT,
    detail_en      TEXT,
    locations      JSONB NOT NULL,     -- [{block_id, span, locator}]
    evidence       JSONB,              -- module-specific payload
    suggested_fix  JSONB,
    dedup_key      TEXT,               -- merge overlapping findings
    dismissed      BOOLEAN NOT NULL DEFAULT FALSE,
    dismiss_reason TEXT,               -- the author's right of reply
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_findings_analysis   ON findings(analysis_id, severity, module);
CREATE UNIQUE INDEX idx_findings_dedup ON findings(analysis_id, dedup_key)
       WHERE dedup_key IS NOT NULL;

-- ─── Citations ───────────────────────────────────────────────
CREATE TABLE references_extracted (
    id             UUID PRIMARY KEY,
    document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal        INT  NOT NULL,
    raw            TEXT NOT NULL,
    parsed         JSONB,
    parse_confidence NUMERIC(4,3),
    resolution_status TEXT,     -- resolved|ambiguous|unresolved|error
    resolved_source   TEXT,     -- crossref|openalex|arxiv|datacite|pubmed
    resolved_doi      TEXT,
    resolved_metadata JSONB,
    is_retracted   BOOLEAN DEFAULT FALSE,
    cited_count    INT DEFAULT 0        -- in-text occurrences; 0 ⇒ uncited
);
CREATE INDEX idx_refs_doc ON references_extracted(document_id, ordinal);

-- ─── Similarity ──────────────────────────────────────────────
CREATE TABLE similarity_matches (
    id            UUID PRIMARY KEY,
    analysis_id   UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,   -- internal|corpus|web
    src_char_start INT NOT NULL,
    src_char_end   INT NOT NULL,
    source_ref     JSONB NOT NULL, -- {url|corpus_doc_id|block_id, title, snippet}
    lexical_sim    NUMERIC(4,3),
    semantic_sim   NUMERIC(4,3),
    is_paraphrase  BOOLEAN,        -- semantic high, lexical low
    matched_words  INT
);
CREATE INDEX idx_simmatch_analysis ON similarity_matches(analysis_id, kind);

-- ─── Reference corpus (never contains user uploads) ──────────
CREATE TABLE corpus_documents (
    id          UUID PRIMARY KEY,
    origin      TEXT NOT NULL,     -- openalex|arxiv|core|manual
    external_id TEXT,
    title       TEXT,
    url         TEXT,
    lang        TEXT,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT corpus_never_user_upload CHECK (origin <> 'user_upload')
);
CREATE TABLE corpus_segments (
    id          BIGSERIAL PRIMARY KEY,
    corpus_doc_id UUID NOT NULL REFERENCES corpus_documents(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    simhash     BIGINT,
    embedding   VECTOR(768)
);
CREATE INDEX idx_corpus_emb ON corpus_segments
       USING hnsw (embedding vector_cosine_ops);

-- ─── Caching & audit ─────────────────────────────────────────
CREATE TABLE external_cache (
    cache_key   CHAR(64) PRIMARY KEY,   -- sha256(provider|normalised_query)
    provider    TEXT NOT NULL,
    payload     JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_cache_expiry ON external_cache(expires_at);

CREATE TABLE egress_log (
    id          BIGSERIAL PRIMARY KEY,
    analysis_id UUID REFERENCES analyses(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    bytes_sent  INT  NOT NULL,
    cache_hit   BOOLEAN NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    actor_id   UUID,
    action     TEXT NOT NULL,   -- upload|analyse|download|delete|consent_change
    object_type TEXT, object_id UUID,
    ip_hash    CHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 2.1 Notes on specific choices

**`egress_log` is a security control, not telemetry.** It is what lets you show a user exactly what left the machine — the transparency promise from doc 02 is only credible if it is enforced by a table.

**The `corpus_never_user_upload` CHECK constraint** encodes the "we never add your thesis to our corpus" promise at the database level. A constraint the database enforces is worth more than a sentence in a privacy policy, and it is a nice detail to point at during the defence.

**`module_runs` exists so partial results are first-class.** When similarity times out, the analysis is `partial`, not `failed`, and the report honestly states which module did not complete.

**Vector storage abstraction.** Define a `VectorIndex` interface with `pgvector` and `sqlite-vec` implementations. On the demo profile with a small corpus, brute-force NumPy cosine over a few thousand segments is entirely adequate — do not over-engineer this.

## 3. Retention

```sql
-- Runs hourly.
UPDATE documents SET deleted_at = now(), storage_key = NULL
 WHERE purge_after < now() AND deleted_at IS NULL;

DELETE FROM document_ir
 WHERE document_id IN (SELECT id FROM documents WHERE deleted_at IS NOT NULL);

DELETE FROM external_cache WHERE expires_at < now();
```

Blob deletion must be driven from the same job so storage and the database cannot drift. Test the cascade — an untested delete path is an untrue privacy claim.

---

## PART B — PIPELINE

## 4. Pipeline as a DAG

```
                        ┌──────────┐
                        │  UPLOAD  │  validate · sniff · store
                        └────┬─────┘
                             ▼
                        ┌──────────┐
                        │  PARSE   │  sandboxed → Document IR
                        └────┬─────┘
                             ▼
                        ┌──────────┐
                        │ ENRICH   │  lang · sections · segments
                        │          │  exclusion mask · references
                        └────┬─────┘
             ┌───────────┬───┴────┬─────────────┬────────────┐
             ▼           ▼        ▼             ▼            ▼
        ┌────────┐ ┌─────────┐ ┌──────┐  ┌───────────┐ ┌──────────┐
        │ FORMAT │ │ GRAMMAR │ │ AIGT │  │ CITATIONS │ │  EMBED   │
        │  ~2 s  │ │  ~30 s  │ │ ~60s │  │   ~90 s   │ │  ~45 s   │
        └────┬───┘ └────┬────┘ └───┬──┘  └─────┬─────┘ └────┬─────┘
             │          │          │           │            ▼
             │          │          │           │      ┌───────────┐
             │          │          │           │      │SIMILARITY │
             │          │          │           │      │  ~120 s   │
             │          │          │           │      └─────┬─────┘
             └──────────┴──────────┴───────────┴────────────┘
                                   ▼
                          ┌─────────────────┐
                          │    AGGREGATE    │  dedup · score
                          └────────┬────────┘
                                   ▼
                          ┌─────────────────┐
                          │     REPORT      │  HTML · PDF · JSON
                          └─────────────────┘
```

**Only two hard dependencies exist:** everything needs `ENRICH`, and `SIMILARITY` needs `EMBED`. The five analysers are otherwise mutually independent — which is exactly why they can be built, tested, shipped, and *cut* independently.

## 5. Stage contracts

| Stage | Input | Output | Timeout | On failure |
|---|---|---|---|---|
| UPLOAD | multipart | `documents` row + blob | 30 s | Reject with a specific reason |
| PARSE | blob | `document_ir` | 120 s | **Fatal** — abort the analysis |
| ENRICH | IR | `segments`, `references_extracted` | 60 s | **Fatal** |
| FORMAT | IR + ruleset | findings | 30 s | Mark `error`, continue |
| GRAMMAR | segments | findings | 300 s | Mark `timeout`, continue |
| AIGT | segments (≥150 words) | findings + probabilities | 180 s | Mark `error`, continue |
| CITATIONS | references | findings + resolutions | 300 s | Partial results acceptable |
| EMBED | segments | vectors | 120 s | Skip → similarity degrades to L1 only |
| SIMILARITY | segments + vectors | matches + findings | 600 s | Partial results acceptable |
| AGGREGATE | all findings | scores | 10 s | **Fatal** |
| REPORT | scores + findings | artefacts | 30 s | Retry once |

**Only PARSE, ENRICH, AGGREGATE are fatal.** Every analyser is optional. This is what makes the system robust in a live demo, where the least reliable component is whatever depends on the network.

## 6. Orchestration

```python
@dataclass(frozen=True)
class Stage:
    name: str
    fn: Callable[[Context], list[Finding]]
    depends_on: tuple[str, ...] = ()
    timeout_s: int = 120
    fatal: bool = False
    requires_consent: str | None = None   # e.g. "external_web"

PIPELINE = [
    Stage("parse",      run_parse,      timeout_s=120, fatal=True),
    Stage("enrich",     run_enrich,     ("parse",),    timeout_s=60, fatal=True),
    Stage("format",     run_format,     ("enrich",),   timeout_s=30),
    Stage("grammar",    run_grammar,    ("enrich",),   timeout_s=300),
    Stage("aigt",       run_aigt,       ("enrich",),   timeout_s=180),
    Stage("citations",  run_citations,  ("enrich",),   timeout_s=300,
          requires_consent="external_metadata"),
    Stage("embed",      run_embed,      ("enrich",),   timeout_s=120),
    Stage("similarity", run_similarity, ("embed",),    timeout_s=600),
    Stage("aggregate",  run_aggregate,  ("format","grammar","aigt",
                                         "citations","similarity"), fatal=True),
    Stage("report",     run_report,     ("aggregate",), timeout_s=30),
]

async def execute(analysis_id: UUID) -> None:
    ctx = Context.load(analysis_id)
    done: set[str] = set()

    for group in topological_groups(PIPELINE):
        runnable = [s for s in group if set(s.depends_on) <= done]
        results = await asyncio.gather(
            *(_run_stage(s, ctx) for s in runnable), return_exceptions=True
        )
        for stage, result in zip(runnable, results):
            if isinstance(result, Exception):
                mark_module(analysis_id, stage.name, _classify(result), result)
                if stage.fatal:
                    set_status(analysis_id, "failed")
                    return
            else:
                persist_findings(analysis_id, result)
                mark_module(analysis_id, stage.name, "ok")
                done.add(stage.name)

    set_status(analysis_id, "partial" if any_module_failed(analysis_id)
                            else "completed")
```

Independent stages within a topological group run concurrently. On the demo profile, set the group concurrency to 2 — with 2 vCPUs, more parallelism makes it slower, not faster.

**Consent enforcement belongs in the orchestrator**, not inside each module. A stage whose `requires_consent` was not granted is marked `skipped` and never invoked. One enforcement point, one thing to audit, one thing to test.

## 7. Progress reporting

Analyses take minutes; a spinner with no information is an abandonment machine. Emit per-stage progress over SSE and stream findings into the UI as each module lands, so results appear progressively rather than in one final dump.

```
event: stage
data: {"stage":"grammar","status":"running","pct":45}

event: findings
data: {"module":"format","count":12}

event: done
data: {"status":"partial","readiness":72.5,
       "failed_modules":["similarity"]}
```

## 8. Caching strategy

Three layers, each with a distinct key. This is what keeps repeat runs fast and external cost bounded.

| Layer | Key | TTL | Effect |
|---|---|---|---|
| **Parse cache** | `sha256(file)` | 7 d | Re-analysing an unchanged file skips parsing entirely |
| **Segment analysis cache** | `sha256(segment_text + module + model_version)` | 30 d | **The big one.** Between drafts, ~90% of sentences are unchanged, so grammar/AIGT/embedding results are reused. Turns a re-check from minutes into seconds. |
| **External API cache** | `sha256(provider + normalised_query)` | 90 d (metadata), 7 d (web) | Crossref/OpenAlex results are highly repetitive across theses |

The segment cache is the single highest-value performance feature in the system, because the dominant real usage pattern is *"I fixed some things, check it again"*. Build it in Phase 2, not as an afterthought.

## 9. Performance budget

Target for an 80-page thesis, ~18 000 words, on 2 vCPU / 16 GB:

| Stage | Cold | Warm cache |
|---|---|---|
| Parse | 8–25 s (PDF slowest) | 0 s |
| Enrich | 5 s | 1 s |
| Format | 2 s | 2 s |
| Grammar | 40 s | 5 s |
| AIGT | 60 s | 8 s |
| Embed | 45 s | 5 s |
| Similarity (L1+L2) | 90 s | 30 s |
| Citations | 90 s (network-bound) | 10 s |
| Aggregate + report | 12 s | 12 s |
| **Total (parallelised)** | **~3–4 min** | **~45 s** |

Measure these from day one and record them in `module_runs.duration_ms`. You get the performance chapter of the thesis for free, and you find regressions immediately.

## 10. Idempotency & retries

- Analyses are **idempotent by `(document_sha256, module_set, engine_version, ruleset_version)`**. Re-requesting an identical analysis returns the existing result instead of recomputing. This also stops double-click duplicate submissions.
- Retry **only** transient failures (network, 5xx, rate limit) with exponential backoff plus jitter, max 3 attempts. Never retry parse errors or validation failures — the input will not have changed.
- Every write is scoped to `analysis_id`, so a retried stage deletes its own prior findings first (`DELETE FROM findings WHERE analysis_id = ? AND module = ?`) and re-inserts. Partial-write duplicates are the classic bug here.

## 11. Testing the pipeline

| Level | What |
|---|---|
| Unit | Each stage against a fixture IR; golden-file assertions on findings |
| Contract | Every parser emits IR that validates against the JSON Schema |
| Integration | Full DAG on 5 real theses (PDF/DOCX/TEX, PL and EN) |
| Failure injection | Force timeout/exception per stage; assert `partial` status and a coherent report |
| Consent | Assert stages are skipped and **zero** egress occurs without consent |
| Performance | Assert the budget in §9 with a tolerance; fail CI on regression |
| Determinism | Same input + same versions ⇒ identical findings (seed everything) |

Determinism matters more than it looks: without it you cannot do golden-file testing, you cannot reproduce an experiment, and you cannot explain to a user why the same document scored differently twice.
