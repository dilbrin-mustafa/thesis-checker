"""Universal finding object (doc 01 §3.3).

Every analyser emits this shape so the aggregator and UI need no
per-module logic. Bilingual titles/details are mandatory.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.ir import Locator, Span

Module = Literal["format", "grammar", "similarity", "aigt", "citation"]
Severity = Literal["blocker", "error", "warning", "info"]


class FindingLocation(BaseModel):
    block_id: str
    span: Span | None = None
    locator: Locator | None = None


class SuggestedFix(BaseModel):
    type: Literal["replace", "manual"] = "manual"
    text: str | None = None


class Finding(BaseModel):
    id: str
    module: Module
    rule_id: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    title_pl: str
    title_en: str
    detail_pl: str | None = None
    detail_en: str | None = None
    locations: list[FindingLocation] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_fix: SuggestedFix | None = None
    docs_url: str | None = None
