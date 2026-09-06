# HF Space wrapper (doc 04) — ships in Phase 6

The Space is a **thin deployment wrapper** over this repo, not a fork.
Planned layout:

```
hf-space/
├── Dockerfile          # multi-stage: frontend build + python runtime, uid 1000, port 7860
├── README.md           # YAML front-matter Space config (sdk, app_port, ...)
├── requirements-demo.txt
└── app/                # subtree/symlink of backend + built frontend
```

Key constraints to honour (doc 04 §4–§6): listen on `0.0.0.0:7860`, run as
uid 1000, `HF_HOME` writable, weights baked into the image, `APP_PROFILE=demo`,
60-page cap, in-memory processing, delete-after-report, demo banner.
