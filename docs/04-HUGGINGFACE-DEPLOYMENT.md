# 04 — Hugging Face Spaces Deployment (Demo Profile)

Goal: a public URL your promoter and reviewers can open, upload a thesis to, and get a real report from — for free, and without compromising the design of the full system.

---

## 1. What the free tier actually gives you

| Resource | Free (CPU Basic) | Consequence for this project |
|---|---|---|
| CPU / RAM | 2 vCPU, 16 GB | 16 GB RAM is generous; **2 vCPU is the real constraint** — this is a CPU-bound NLP workload |
| Disk | ~50 GB, **ephemeral** | Wiped on every restart/rebuild. Exceeding it evicts the workload |
| Persistence | **None** on free tier | Paid add-on, or use an external store / private HF Dataset repo |
| Sleep | After 48 h idle; 30–90 s cold start | Wake it before a demo |
| Repo size | ~1 GB soft limit | Ship no model weights in the repo — pull from the Hub at build time |
| Port | **7860, hardcoded** | Docker Spaces must listen on 7860 |
| Visibility | Public by default | Private Spaces are a paid feature — **assume the world can read your repo** |
| Secrets | Settings → Secrets, injected as env vars | The only safe place for API keys |
| GPU | **ZeroGPU** — time-sliced datacenter GPU, `@spaces.GPU` | Real and free, but with sharp constraints — see §2A |

Two of these drive every decision below: **2 vCPU** and **ephemeral disk**.

---

## 2A. ZeroGPU — what it actually gives you

ZeroGPU is genuinely free and not paywalled for *use*. But four properties make it a poor fit for the bulk of this pipeline and an excellent fit for one specific part of it.

| Property | Detail | Consequence here |
|---|---|---|
| Allocation model | Per-function. `@spaces.GPU` grabs a pooled GPU for the call, releases on return. Idle costs nothing. | Only decorated inference gets GPU. Parsing, rules, LanguageTool, citation HTTP stay on the 2 vCPU. |
| **Per-call duration cap** | Default 60 s; **~120 s hard cap on the free tier** (`ZeroGPU illegal duration` above it) | **This is the binding constraint.** A whole-thesis analysis (3–4 min) can never be one GPU call. Work must be chunked. |
| **Quota is the visitor's, not yours** | Anonymous ~2 min/day, free account ~5 min/day, PRO ~25–40 min/day. Consumed in seconds of actual runtime. | Your reviewer, opening the Space anonymously, has ~2 minutes of GPU for the day. Budget the demo against *that*, not against your own quota. |
| Quota **pre-check** | The platform compares your *declared* `duration` against remaining quota, not actual runtime. A 10 s task declared at the 60 s default fails once the visitor has < 60 s left. | **Always declare the smallest realistic duration.** Also ranks you higher in the queue. |
| SDK support | Gradio is the first-class path; PyTorch-only | A Docker + FastAPI + React Space is the harder path. See §2B. |
| Hosting a ZeroGPU Space | Sources conflict on whether *creating* one requires PRO (several current sources say PRO is needed to host, while quota for *visitors* is free) | **Verify this on your own account early.** If hosting needs PRO, that's $9/mo — cheap, but you need to know in month 1, not week 30. |

### What this means for the design

The instinct is "GPU is free, so run everything on GPU". That is the wrong conclusion. With a ~120 s per-call cap and a visitor holding ~2–5 min/day, GPU time is the *scarcest* resource in the demo — scarcer than CPU, which is unmetered.

So: **spend GPU only where the CPU/GPU speedup ratio is largest, and where the work fits in one short call.**

| Stage | GPU-worthy? | Why |
|---|---|---|
| **AIGT inference** (XLM-R / HerBERT classifier) | **Yes — best candidate** | Transformer forward passes; 10–20× faster; batches an 80-page thesis into ~5–15 s |
| **Embeddings** (e5) for similarity | **Yes — second** | Same reason; ~45 s CPU → ~5 s GPU |
| Binoculars / perplexity AIGT | Only if quota allows | Needs two LMs resident; heavy. Keep CPU-only or omit from demo. |
| LLM academic-style pass | No | A useful model won't fit the duration/VRAM budget sanely; use the Inference API instead |
| Parse, format rules, LanguageTool, citations | No | CPU-bound or network-bound; GPU gives nothing |

**Realistic GPU budget for one demo analysis: ~20–30 seconds.** That fits inside an anonymous visitor's ~2 min/day, so a reviewer can run three or four analyses. That is the number to design to.

### Implementation shape

```python
import spaces, torch

# Load at MODULE level and .to("cuda") eagerly — ZeroGPU intercepts this
# before a real GPU is attached and moves the model on call.
aigt_model = AutoModelForSequenceClassification.from_pretrained(
    "<you>/thesisguard-aigt-xlmr", torch_dtype=torch.float16).to("cuda")
embed_model = SentenceTransformer("intfloat/multilingual-e5-base").to("cuda")

def _estimate_aigt_duration(segments: list[str]) -> int:
    # Dynamic duration: declare the smallest realistic value per request.
    return max(10, min(90, 5 + len(segments) // 40))

@spaces.GPU(duration=_estimate_aigt_duration)
def aigt_batch(segments: list[str]) -> list[float]:
    # Pure GPU work only. No file I/O, no HTTP, no CPU pre/post —
    # anything inside the decorator burns the visitor's quota.
    with torch.inference_mode():
        return _score(aigt_model, segments)

@spaces.GPU(duration=25)
def embed_batch(segments: list[str]) -> np.ndarray:
    with torch.inference_mode():
        return embed_model.encode(segments, batch_size=64,
                                  normalize_embeddings=True)
```

Rules that follow directly from the constraints above:

1. **Chunk to stay under the cap.** Never wrap the whole analysis. For a long thesis, split into several sub-120 s calls — but note each call re-checks quota, so fewer, well-sized calls beat many tiny ones.
2. **Keep CPU work outside the decorator.** Tokenisation, I/O, and post-processing inside a `@spaces.GPU` function bill the visitor for CPU time. Pre-tokenise on CPU, pass tensors in.
3. **Dynamic `duration`.** A callable that estimates per request avoids both premature `quota exceeded` and queue deprioritisation.
4. **Mandatory CPU fallback.** Catch `gr.Error` / quota-exceeded and fall back to the quantised CPU model with a UI notice: *"GPU quota exhausted — using the lightweight CPU model, results may differ slightly."* Without this, your demo simply dies for the fourth visitor of the day, which could be your reviewer.
5. **`spaces` is a no-op off-platform**, so the same code runs locally and in the full deployment. Do not build your own fallback shim.

## 2B. Architecture choice: Gradio vs Docker

ZeroGPU's well-trodden path is Gradio; Docker Spaces are supported but with more friction and less documentation. That collides with the React + FastAPI design in doc 01.

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **A. Gradio Space, ZeroGPU** | Simplest, best-supported ZeroGPU path; fastest to working demo | Not your real UI; a second frontend to maintain | **Recommended for the demo** |
| **B. Docker Space (FastAPI + React), CPU only** | Your actual product, honestly demoed | No GPU; slower; quantised models | Good fallback |
| **C. Docker Space + ZeroGPU** | Real UI *and* GPU | Least documented, most fragile, most of your time | Only if A and B both disappoint |
| **D. Docker Space (UI) + separate Gradio ZeroGPU Space (inference API)** | Real UI, GPU where it matters, clean separation | Two Spaces; cross-Space latency; two quotas | **Best of both if you have time** |

**Recommendation: start with A, keep D as the stretch.** Build the analysis engine as a library with a thin Gradio wrapper — `gr.File` upload, `gr.HTML` report, `gr.JSON` raw output. That is a genuinely good demo in a day or two, and because the engine is a library, the React app in the full deployment calls exactly the same code. Option D then becomes a small refactor rather than a rewrite, and you can decide in Phase 6 whether it's worth it.

This makes the `APP_PROFILE=demo` split from doc 01 even more valuable: same engine, three possible frontends.

## 2. Demo profile: what to cut

Same codebase, `APP_PROFILE=demo`. Cuts, and how to present them:

| Component | Full | Demo | Shown in UI as |
|---|---|---|---|
| Queue | Celery + Redis | in-process asyncio, concurrency 1 | queue position |
| DB | PostgreSQL + pgvector | SQLite + brute-force NumPy cosine | — |
| Blobs | MinIO | `/tmp`, deleted after parse | — |
| Embeddings | `multilingual-e5-base` | **e5-base on ZeroGPU**; e5-small ONNX int8 as CPU fallback | — |
| AIGT | Fine-tuned XLM-R + Binoculars | **Full XLM-R on ZeroGPU**; distilled int8 on CPU fallback | GPU/CPU mode shown in the report |
| Grammar | LanguageTool server | LanguageTool `n-gram`-free, or public API w/ rate limit | — |
| Similarity | L1 + L2 + L3 web | **L1 internal only** (+ tiny demo corpus) | "Web search disabled in the demo" |
| Citations | Full | Full — cheap and network-bound, keep it | — |
| Page cap | 500 | **60** | stated at upload |
| Retention | 24 h | **Deleted immediately after the report** | stated at upload |
| Accounts | OIDC | none, anonymous session | — |

Keep **format, grammar, citations, and internal duplication** — they are fast, offline, deterministic, and demo well. Similarity-over-the-web and heavyweight AIGT are what blow the budget; degrade them honestly rather than hiding them.

> A demo that clearly labels its reduced capability reads as engineering maturity. A demo that silently returns worse results reads as a broken product.

## 3. Repository layout for the Space

Keep the Space as a **thin deployment wrapper** over your real repo — either a git subtree/submodule, or a CI job that pushes a built artefact. Do not maintain a divergent copy.

```
hf-space/
├── Dockerfile
├── README.md          # YAML front-matter configures the Space
├── requirements-demo.txt
└── app/               # symlink/subtree of backend + built frontend
```

**`README.md` front-matter** (required — this *is* the Space config):

```yaml
---
title: ThesisGuard — Automatic Thesis Checker
emoji: 🎓
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Pre-submission quality analysis for PL/EN engineering theses
---
```

## 4. Dockerfile

```dockerfile
# ---- frontend build ----
FROM node:20-slim AS fe
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build                      # → /fe/dist

# ---- runtime ----
FROM python:3.12-slim

# HF Spaces runs as uid 1000; everything must be writable by that user.
RUN useradd -m -u 1000 user
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 default-jre-headless libgl1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/user/app
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/home/user/.cache/huggingface \
    APP_PROFILE=demo \
    PORT=7860

COPY --chown=user requirements-demo.txt .
RUN pip install --no-cache-dir -r requirements-demo.txt

# Bake model weights into the image so cold start doesn't download them.
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('intfloat/multilingual-e5-small'); \
snapshot_download('<your-org>/thesisguard-aigt-distil')"

COPY --chown=user backend/ ./backend/
COPY --from=fe --chown=user /fe/dist ./static/

USER user
EXPOSE 7860
CMD ["uvicorn", "backend.app.main:app", \
     "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
```

Four things here are load-bearing and each is a common failure:

1. **`--host 0.0.0.0` and port 7860.** Binding to `127.0.0.1` produces a Space that builds fine and serves nothing.
2. **uid 1000 + `--chown=user`.** Spaces run non-root; writing to a root-owned path fails at runtime, not build time.
3. **`HF_HOME` under the user's home.** Default cache locations are not writable and the failure message is unhelpful.
4. **Baking weights into the image.** Downloading at startup means every cold start after a 48 h sleep re-downloads hundreds of MB. Bake them and cold start stays under ~30 s.

`--workers 1` is deliberate: with 2 vCPU, multiple uvicorn workers each loading a copy of the models is the fastest route to an OOM.

## 5. Persistence without persistent storage

Ephemeral disk is fine here, because the demo genuinely should not retain user theses. But two things are worth keeping across restarts:

| Data | Strategy |
|---|---|
| User uploads & reports | **Do not persist.** Delete after the report is served. This is a feature — state it in the UI. |
| External API cache (Crossref/OpenAlex) | Periodically push the SQLite cache to a **private HF Dataset repo** via `huggingface_hub`; pull on boot. Free, and avoids re-querying on every rebuild. |
| Anonymous usage metrics | Same private-Dataset pattern, append-only JSONL. Useful data for the thesis evaluation chapter. |
| Model weights | Baked into the image (§4). |

```python
# On boot, best-effort restore; on shutdown/interval, best-effort snapshot.
def restore_cache() -> None:
    try:
        hf_hub_download("<you>/thesisguard-cache", "external_cache.sqlite",
                        repo_type="dataset", local_dir="/home/user/app/data",
                        token=os.environ["HF_TOKEN"])
    except Exception:
        log.warning("cache cold-start: no snapshot available")
```

Wrap both directions in try/except. A cache is an optimisation; a Space that refuses to boot because a cache snapshot is missing is a self-inflicted outage.

## 6. Demo-specific guardrails

The Space is a public endpoint with no accounts. Assume abuse.

```python
DEMO_LIMITS = dict(
    max_upload_mb=15,
    max_pages=60,
    max_words=30_000,
    analyses_per_ip_per_hour=3,
    concurrent_analyses=1,       # global — 2 vCPU
    queue_max_depth=5,
    total_timeout_s=300,
    external_queries_per_doc=50,
)
```

- **Global concurrency of 1** with a visible queue. Two concurrent analyses on 2 vCPU means both are slow and users assume it is broken.
- **Reject, don't queue, past depth 5** — with an honest "demo is busy, try again shortly".
- **Delete on completion**, and say so at upload: *"Files are processed in memory and deleted immediately. Nothing is stored, and your thesis is never added to any comparison corpus."*
- **A prominent demo banner:** reduced models, no web plagiarism search, 60-page cap, link to the full system and to the methodology page.
- **Keys in Space secrets only.** The repo is public — `gitleaks` in CI is not paranoia here, it is the minimum.

## 7. Cold-start and demo-day operations

Free Spaces sleep after 48 h idle, and the first request pays 30–90 s.

- **Before any demo or defence, open the Space 10 minutes early** to warm it. Do not discover the cold start in front of your committee.
- Add a `/health` endpoint that loads nothing, so uptime pings are cheap.
- Preload models at startup, not on first request, so the first user does not pay the model-load cost on top of the container cold start.
- **Record a 3-minute screencast of a full successful run** and keep it in the repo. Free-tier infrastructure fails at inconvenient moments; a fallback video has saved many defences.

## 8. Alternatives if the free tier proves too tight

| Option | Cost | When |
|---|---|---|
| HF persistent storage | $5/mo | If you need the demo to retain state |
| HF Pro | $9/mo | Private Space, ZeroGPU quota, better CPU |
| Split: Space (UI+light) + Colab/local for heavy | free | If AIGT inference is too slow on CPU |
| Fly.io / Render free tier | free | Alternative host; similar constraints |
| **Local Docker Compose + screencast** | free | Perfectly acceptable for a defence. Do not spend three weeks fighting a free tier to save a $9 subscription. |

Budget guidance: the demo is worth roughly one week of effort. If it starts consuming more, that time is better spent on the AI-detection evaluation, which is what actually carries the grade.

## 9. Deployment checklist

- [ ] `README.md` front-matter present with `sdk: docker`, `app_port: 7860`
- [ ] Container listens on `0.0.0.0:7860`
- [ ] Runs as uid 1000; all app paths chowned
- [ ] `HF_HOME` set to a writable path
- [ ] Model weights baked into the image
- [ ] `APP_PROFILE=demo` enforced
- [ ] All demo limits active and unit-tested
- [ ] Files deleted after the report is served (verified)
- [ ] Demo-limitations banner visible
- [ ] Secrets in Space settings; `gitleaks` green
- [ ] Image and runtime disk well under 50 GB
- [ ] Cold start measured and under ~90 s
- [ ] `/health` endpoint responds
- [ ] Fallback screencast recorded

**ZeroGPU**
- [ ] Confirmed on your own account whether hosting a ZeroGPU Space requires PRO
- [ ] Models instantiated at module scope with eager `.to("cuda")`
- [ ] Every `@spaces.GPU` declares a realistic (small) `duration`; dynamic where input size varies
- [ ] No call can exceed ~120 s; long work is chunked
- [ ] No file I/O, HTTP, or heavy CPU work inside a decorated function
- [ ] CPU fallback triggers on quota-exceeded, with a visible UI notice
- [ ] Per-analysis GPU budget measured and ≤ ~30 s
- [ ] `spaces` in `requirements.txt` (no home-made shim)
