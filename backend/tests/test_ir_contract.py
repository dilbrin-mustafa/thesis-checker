"""IR contract tests (Phase 0 exit criterion: schema frozen + tested).

Every parser in Phase 1 MUST pass `assert_valid_ir` — import it from here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.finding import Finding
from app.models.ir import DocumentIR

FIXTURES = Path(__file__).parent / "fixtures"


def assert_valid_ir(ir: DocumentIR) -> None:
    """Contract every parser must satisfy (doc 01 §3.2)."""
    # 1. Offsets must slice back to the block text.
    for block in ir.blocks:
        assert 0 <= block.span.start <= block.span.end <= len(ir.plain_text), block.id
        assert ir.block_text_slice(block) == block.text, (
            f"block {block.id}: span does not reproduce text"
        )
    # 2. Sections reference existing blocks.
    block_ids = {b.id for b in ir.blocks}
    for section in ir.sections:
        for bid in section.block_ids:
            assert bid in block_ids, f"section {section.id} -> unknown block {bid}"
    # 3. Citations reference existing blocks.
    for citation in ir.citations:
        assert citation.block_id in block_ids, f"citation {citation.id} -> unknown block"
    # 4. Exclusion ranges are within bounds.
    for exc in ir.exclusion_mask:
        assert 0 <= exc.start <= exc.end <= len(ir.plain_text)


def load_golden() -> DocumentIR:
    raw = json.loads((FIXTURES / "golden_ir.json").read_text(encoding="utf-8"))
    return DocumentIR.model_validate(raw)


def test_golden_fixture_validates() -> None:
    ir = load_golden()
    assert ir.schema_version == "1.0"
    assert len(ir.blocks) == 4


def test_golden_fixture_passes_contract() -> None:
    assert_valid_ir(load_golden())


def test_exclusion_helper() -> None:
    ir = load_golden()
    assert ir.is_excluded(35, 40) is True  # inside the abstract exclusion
    assert ir.is_excluded(6, 10) is False  # body paragraph


def test_rejects_bad_sha256() -> None:
    bad = json.loads((FIXTURES / "golden_ir.json").read_text(encoding="utf-8"))
    bad["source"]["sha256"] = "zzz"
    with pytest.raises(ValidationError):
        DocumentIR.model_validate(bad)


def test_finding_model_roundtrip() -> None:
    f = Finding(
        id="f1",
        module="format",
        rule_id="PG.FONT.BODY",
        severity="error",
        confidence=0.9,
        title_pl="Nieprawidłowa czcionka",
        title_en="Incorrect font",
    )
    assert Finding.model_validate(f.model_dump()).rule_id == "PG.FONT.BODY"
