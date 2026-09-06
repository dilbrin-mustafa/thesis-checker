# ThesisGuard — Automatic Thesis Checking System

Pre-submission advisory system for bilingual (PL/EN) engineering theses.
MSc thesis project @ Gdańsk University of Technology.

> **Scope:** This is a *pre-submission advisory tool*, explicitly **not** a JSA
> replacement and never an accusation engine. Every finding is a signal with
> confidence and evidence, for a human to adjudicate.

## Docs

| Doc | Content |
|-----|---------|
| `PLAN.md` | Original project plan (overview) |
| `docs/01-BLUEPRINT.md` | Complete blueprint: architecture, IR, modules, stack |
| `docs/02-SECURITY-AND-GUARDRAILS.md` | Security, privacy/GDPR, ethical guardrails |
| `docs/03-DATABASE-AND-PIPELINE.md` | DB schema, DAG pipeline, caching |
| `docs/04-HUGGINGFACE-DEPLOYMENT.md` | HF Spaces demo profile |
| `docs/05-IMPLEMENTATION-PLAN.md` | Phase-by-phase execution plan (38 weeks) |
| `docs/journal.md` | Weekly engineering log (fills the methodology chapter) |

## Quickstart (Phase 0)

```bash
make install   # create venv + install deps
make test      # run test suite
make run       # run API locally (http://localhost:8000, /health, /docs)
make fmt       # ruff format + lint
make typecheck # mypy strict
```

Full stack (requires Docker):

```bash
make up        # postgres+pgvector, redis, api, languagetool
make down
```

## Layout

```
thesis-checker/
├── backend/app/        # FastAPI + pipeline (api, core, ingestion, analysis, ...)
├── backend/tests/      # unit / contract / integration tests + fixtures
├── frontend/           # React + TS + Vite (scaffolded in Phase 2)
├── rules/pg/           # YAML rulesets (ZR 49/2014), Phase 2
├── research/           # corpus, experiments, results (never imported by backend/)
├── deploy/             # docker-compose.yml, Dockerfiles, hf-space/
├── docs/               # design docs + journal + ADRs
└── Makefile
```

## Profiles

One codebase, two profiles via `APP_PROFILE`:

- `full` — Postgres+pgvector, Redis/Celery, full models (local Docker Compose)
- `demo` — SQLite, in-process worker, quantised models (HF Spaces)

## Status

- [x] Phase 0 — Foundations (repo, CI, IR contract frozen)
- [ ] Phase 1 — Ingestion (DOCX → LaTeX → PDF)
- [ ] Phase 2 — Format + Grammar + vertical slice
- [ ] Phase 3 — Citations
- [ ] Phase 4 — Similarity
- [ ] Phase 5 — AIGT research core
- [ ] Phase 6 — Aggregation + UX + deployment
- [ ] Phase 7 — Evaluation + writing
