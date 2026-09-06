"""API routes. Phase 0: /health only. Full routers land Phase 2 (vertical slice)."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import ENGINE_VERSION, get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "engine_version": ENGINE_VERSION,
        "profile": settings.app_profile,
    }
