"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import ENGINE_VERSION

app = FastAPI(
    title="ThesisGuard",
    description="Pre-submission advisory system for bilingual (PL/EN) engineering theses.",
    version=ENGINE_VERSION,
)

app.include_router(router)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "ThesisGuard", "docs": "/docs", "health": "/health"}
